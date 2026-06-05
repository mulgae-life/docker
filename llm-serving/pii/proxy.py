"""PII 프록시 — 외부 :5015 인수, in/out 양방향 PII 검사 후 게이트웨이로 forward.

토폴로지: 클라이언트 → (이 프록시 :5015) → 게이트웨이(127.0.0.1:6015) → vLLM.
- in : messages[].content 를 검사. 고유식별정보(주민/카드 등) 검출 시 차단(422),
       그 외(이름/주소/조직/전화 등)는 마스킹 후 forward.
- out: 응답 content/reasoning/tool_calls 를 마스킹.
       스트리밍은 stream_mode(post=완결 후 1회 / off)로 처리(buffer 점진 flush는 후속).
- fail-closed: NER 풀 전체 장애 시 차단(누출 방지). 구조화 regex는 풀과 무관하게 동작.

기동: cd pii && python proxy.py -c configs/proxy.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from audit import AuditLogger
from config import PiiConfig
from detectors.ner_client import NerPool, NerUnavailable
from hooks import analyze

_OUT_TEXT_FIELDS = ("content", "reasoning", "reasoning_content")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg: PiiConfig = app.state.cfg
    client = httpx.AsyncClient(timeout=cfg.upstream_timeout)
    pool = NerPool(client, score_threshold=0.5)
    for b in cfg.ner_backends:
        pool.add_backend(b.host, b.port, b.model_tag)
    audit = AuditLogger(cfg.audit_log_path, cfg.audit_salt)

    app.state.client = client
    app.state.pool = pool
    app.state.audit = audit

    async def _health_loop() -> None:
        while True:
            await pool.health_check()
            await asyncio.sleep(10)

    task = asyncio.create_task(_health_loop()) if cfg.ner_backends else None
    try:
        yield
    finally:
        if task:
            task.cancel()
        await client.aclose()


def create_app(cfg: PiiConfig) -> FastAPI:
    app = FastAPI(title="PII Proxy", lifespan=lifespan)
    app.state.cfg = cfg

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/v1/models")
    async def models() -> JSONResponse:
        # OpenAI SDK 계열 클라이언트가 model 검증차 호출한다. PII 검사 대상이 아닌
        # 메타 조회이므로 upstream 게이트웨이로 그대로 패스스루(미구현 시 빈 배열 응답 문제).
        cfg: PiiConfig = app.state.cfg
        client: httpx.AsyncClient = app.state.client
        try:
            resp = await client.get(f"{cfg.upstream_url}/v1/models")
        except httpx.TimeoutException:
            return JSONResponse({"error": "백엔드 타임아웃"}, status_code=504)
        except httpx.HTTPError:
            return JSONResponse({"error": "백엔드 연결 실패"}, status_code=502)
        try:
            return JSONResponse(resp.json(), status_code=resp.status_code)
        except json.JSONDecodeError:
            return JSONResponse({"error": "백엔드 에러"}, status_code=resp.status_code)

    @app.post("/v1/chat/completions", response_model=None)
    async def chat(request: Request):
        cfg: PiiConfig = app.state.cfg
        pool: NerPool = app.state.pool
        audit: AuditLogger = app.state.audit
        client: httpx.AsyncClient = app.state.client
        req_id = str(uuid.uuid4())

        raw = await request.body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse({"error": "잘못된 JSON"}, status_code=400)

        # ── ① 요청(in) 검사 ──
        if cfg.in_enabled and isinstance(payload.get("messages"), list):
            try:
                blocked = await _check_in(payload["messages"], cfg, pool, audit, req_id)
            except NerUnavailable:
                if cfg.fail_mode == "closed":
                    return JSONResponse(
                        {"error": {"message": "PII 검사 일시 불가", "type": "pii_unavailable",
                                   "request_id": req_id}},
                        status_code=503)
                blocked = False  # fail-open(명시 설정 시): 구조화 검사만 적용된 채 통과
            if blocked:
                return JSONResponse(
                    {"error": {"message": "입력에 개인정보(주민등록번호·카드번호 등)가 포함되어 차단되었습니다.",
                               "type": "pii_blocked", "request_id": req_id}},
                    status_code=422)

        is_stream = bool(payload.get("stream", False))
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        auth = request.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth
        url = f"{cfg.upstream_url}/v1/chat/completions"

        if is_stream:
            return await _proxy_stream(url, body, headers, cfg, pool, audit, req_id, client)
        return await _proxy_nonstream(url, body, headers, cfg, pool, audit, req_id, client)

    return app


async def _check_in(messages: list[dict], cfg: PiiConfig, pool: NerPool,
                    audit: AuditLogger, req_id: str) -> bool:
    """messages[].content(str) 검사. 마스킹은 제자리 수정, 차단 여부 반환."""
    blocked = False
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, str) or not content:
            continue  # 멀티모달(list) 등 비텍스트는 PoC 범위 외
        res = await analyze(content, pool, block_types=cfg.block_types,
                            connect_to=cfg.ner_connect_timeout, read_to=cfg.ner_read_timeout)
        for d in res.detections:
            audit.record(request_id=req_id, direction="in", entity_type=d.type,
                         action="block" if res.has_block else "mask",
                         value=res.text[d.start:d.end], decision_source=d.source)
        if res.has_block:
            blocked = True
        msg["content"] = res.masked
    return blocked


async def _mask_response_json(data: dict, cfg: PiiConfig, pool: NerPool,
                              audit: AuditLogger, req_id: str) -> dict:
    """비스트림 응답의 choices[].message 텍스트 필드를 마스킹(차단 아닌 마스킹만)."""
    for ch in data.get("choices", []):
        msg = ch.get("message")
        if not isinstance(msg, dict):
            continue
        for field in _OUT_TEXT_FIELDS:
            v = msg.get(field)
            if isinstance(v, str) and v:
                res = await analyze(v, pool, block_types=[],
                                    connect_to=cfg.ner_connect_timeout, read_to=cfg.ner_read_timeout)
                for d in res.detections:
                    audit.record(request_id=req_id, direction="out", entity_type=d.type,
                                 action="mask", value=res.text[d.start:d.end], decision_source=d.source)
                msg[field] = res.masked
    return data


async def _proxy_nonstream(url, body, headers, cfg, pool, audit, req_id, client):
    try:
        resp = await client.post(url, content=body, headers=headers)
    except httpx.TimeoutException:
        return JSONResponse({"error": "백엔드 타임아웃"}, status_code=504)
    except httpx.HTTPError:
        return JSONResponse({"error": "백엔드 연결 실패"}, status_code=502)

    if resp.status_code != 200:
        try:
            return JSONResponse(resp.json(), status_code=resp.status_code)
        except json.JSONDecodeError:
            return JSONResponse({"error": "백엔드 에러"}, status_code=resp.status_code)

    data = resp.json()
    if cfg.out_enabled:
        try:
            data = await _mask_response_json(data, cfg, pool, audit, req_id)
        except NerUnavailable:
            if cfg.fail_mode == "closed":
                return JSONResponse({"error": "PII 검사 불가(응답 보류)"}, status_code=502)
    return JSONResponse(data, status_code=200)


def _sse_delta_text(obj: dict) -> str:
    """SSE 청크의 choices[].delta.content 텍스트를 추출."""
    out = []
    for ch in obj.get("choices", []):
        delta = ch.get("delta") or {}
        for field in ("content", "reasoning", "reasoning_content"):
            v = delta.get(field)
            if isinstance(v, str):
                out.append(v)
    return "".join(out)


async def _proxy_stream(url, body, headers, cfg, pool, audit, req_id, client):
    """스트리밍 프록시.

    stream_mode='post': 게이트웨이 SSE를 끝까지 모아 한 번에 out 검사 → 마스킹된
    내용을 단일 delta로 재방출(누출 0, 토큰 점진성은 포기). buffer 점진 모드는 후속.
    stream_mode='off': 검사 없이 패스스루.
    """
    try:
        req = client.build_request("POST", url, content=body, headers=headers)
        resp = await client.send(req, stream=True)
    except httpx.HTTPError:
        return JSONResponse({"error": "백엔드 연결 실패"}, status_code=502)

    if resp.status_code != 200:
        eb = await resp.aread()
        await resp.aclose()
        try:
            return JSONResponse(json.loads(eb), status_code=resp.status_code)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "백엔드 에러"}, status_code=resp.status_code)

    if not cfg.out_enabled or cfg.stream_mode == "off":
        async def _passthrough() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()
        return StreamingResponse(_passthrough(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # stream_mode='post' (기본): 완결 후 1회 검사 → 마스킹 텍스트를 단일 청크로 재방출
    chunks: list[dict] = []
    try:
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                chunks.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    finally:
        await resp.aclose()

    full = "".join(_sse_delta_text(c) for c in chunks)
    masked = full
    if full:
        try:
            res = await analyze(full, pool, block_types=[],
                                connect_to=cfg.ner_connect_timeout, read_to=cfg.ner_read_timeout)
            for d in res.detections:
                audit.record(request_id=req_id, direction="out", entity_type=d.type,
                             action="mask", value=res.text[d.start:d.end], decision_source=d.source)
            masked = res.masked
        except NerUnavailable:
            if cfg.fail_mode == "closed":
                masked = "[응답이 PII 검사 불가로 보류되었습니다]"

    model = chunks[0].get("model", "") if chunks else ""

    async def _emit() -> AsyncGenerator[bytes, None]:
        head = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": masked}}],
                "model": model}
        yield f"data: {json.dumps(head, ensure_ascii=False)}\n\n".encode()
        done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "model": model}
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(_emit(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def main() -> None:
    p = argparse.ArgumentParser(description="PII 프록시")
    p.add_argument("-c", "--config", required=True, help="설정 yaml 경로")
    args = p.parse_args()
    cfg = PiiConfig.from_yaml(args.config)
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
