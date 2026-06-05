"""PII 프록시 — 외부 :5015 인수, in/out 양방향 PII 검사 후 게이트웨이로 forward.

토폴로지: 클라이언트 → (이 프록시 :5015) → 게이트웨이(127.0.0.1:6015) → vLLM.
- in : messages 텍스트(평문 content + 멀티모달 text 파트 + tool_calls.arguments)를 검사.
       고유식별정보(주민/카드 등) 검출 시 차단(422), 그 외(이름/주소/조직/전화 등)는 마스킹 후 forward.
       ※ 이미지 바이트 자체는 검사 불가(설계상 한계) — 텍스트 파트만 검사.
- out: 응답 content/reasoning/멀티모달 text/tool_calls.arguments 를 마스킹.
       스트리밍은 stream_mode(post=완결 후 1회 / off)로 처리(buffer 점진 flush는 후속).
       post는 구조(id/created/usage/finish_reason, reasoning↔content 분리)를 보존해 재방출한다.
- bypass: allow_bypass=true + 헤더 X-PII-Mode:bypass 면 in/out 검사 통째 생략(감사 기록).
- fail-closed: NER 풀 전체 장애 시 차단(누출 방지). fail-open이라도 구조화 regex는 항상 적용.

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


def _ignore_types(request: Request, cfg: PiiConfig) -> frozenset[str]:
    """요청 헤더 X-PII-Ignore-Types에서 서비스가 끄려는 마스킹 타입을 파싱한다.

    서버 화이트리스트(ignorable_types)와 교집합만 허용하고, 차단 타입(block_types)은
    어떤 경우에도 제외 불가하다(차집합 가드). 예) 문서생성 서비스는 'org'만 전달해
    조직명 노출을 허용하되, 주민/카드/이름/주소는 항상 마스킹된다.
    """
    raw = request.headers.get("x-pii-ignore-types", "")
    requested = {t.strip().lower() for t in raw.split(",") if t.strip()}
    allowed = set(cfg.ignorable_types) - set(cfg.block_types)
    return frozenset(requested & allowed)


def _pii_mode(request: Request, cfg: PiiConfig) -> str:
    """요청 헤더 X-PII-Mode로 전면 우회 여부 결정. 'enforce'(기본) | 'bypass'.

    bypass는 cfg.allow_bypass=True일 때만 유효하다(운영자 명시 opt-in). 미허용 환경에서
    헤더가 와도 enforce로 강제해 '헤더 하나로 우회'를 막는다.
    """
    if not cfg.allow_bypass:
        return "enforce"
    mode = request.headers.get("x-pii-mode", "").strip().lower()
    return "bypass" if mode == "bypass" else "enforce"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg: PiiConfig = app.state.cfg
    client = httpx.AsyncClient(timeout=cfg.upstream_timeout)
    pool = NerPool(client, score_threshold=0.5,
                   require_all_backends=cfg.ner_require_all_backends)
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

        ignore = _ignore_types(request, cfg)  # 서비스별 마스킹 토글(예: org)
        mode = _pii_mode(request, cfg)        # enforce(기본) | bypass(allow_bypass 시)
        bypass = mode == "bypass"

        # ── ① 요청(in) 검사 ── (bypass면 통째 생략, 감사만 기록)
        if bypass:
            audit.record(request_id=req_id, direction="in", entity_type="_all",
                         action="bypass", value="", decision_source="header")
        elif cfg.in_enabled and isinstance(payload.get("messages"), list):
            try:
                blocked = await _check_in(payload["messages"], cfg, pool, audit, req_id, ignore)
            except NerUnavailable:
                if cfg.fail_mode == "closed":
                    return JSONResponse(
                        {"error": {"message": "PII 검사 일시 불가", "type": "pii_unavailable",
                                   "request_id": req_id}},
                        status_code=503)
                # fail-open: NER 없이도 구조화 regex(주민/카드)는 반드시 적용해 누출을 막는다.
                blocked = await _check_in(payload["messages"], cfg, None, audit, req_id, ignore)
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
            return await _proxy_stream(url, body, headers, cfg, pool, audit, req_id, client, ignore, bypass)
        return await _proxy_nonstream(url, body, headers, cfg, pool, audit, req_id, client, ignore, bypass)

    return app


async def _scan(text: str, cfg: PiiConfig, pool: NerPool | None, audit: AuditLogger,
                req_id: str, direction: str, ignore: frozenset[str],
                block_types: list[str]) -> tuple[str, bool]:
    """텍스트 1건을 analyze→감사 기록→마스킹. (마스킹된 텍스트, 차단대상 포함 여부) 반환.

    텍스트가 위치한 곳(content str / 멀티모달 text 파트 / tool_calls arguments)에 무관하게
    재사용한다. block_types=[]면 마스킹만(차단 없음).
    """
    res = await analyze(text, pool, block_types=block_types,
                        connect_to=cfg.ner_connect_timeout, read_to=cfg.ner_read_timeout,
                        skip_mask_types=ignore)
    for d in res.detections:
        action = "skip" if res.is_skipped(d) else ("block" if res.has_block else "mask")
        audit.record(request_id=req_id, direction=direction, entity_type=d.type,
                     action=action, value=res.text[d.start:d.end], decision_source=d.source)
    return res.masked, res.has_block


async def _mask_message_texts(msg: dict, cfg: PiiConfig, pool: NerPool | None,
                              audit: AuditLogger, req_id: str, direction: str,
                              ignore: frozenset[str], block_types: list[str]) -> bool:
    """한 메시지의 모든 텍스트 위치를 제자리 마스킹. 차단대상 포함 여부 반환.

    검사 대상:
      - content(str)
      - content(list)의 text 파트 (멀티모달 — 이미지 바이트는 제외).
        type이 "text"/"input_text"/"output_text" 등 무엇이든, str "text" 키가 있으면 검사한다
        (Responses API 등 비표준 키로 우회하는 누출 방지). image_url 등은 text 키가 없어 자연히 제외.
      - tool_calls[].function.arguments / 레거시 function_call.arguments (함수 인자 JSON 안의 PII)
    """
    blocked = False
    content = msg.get("content")
    if isinstance(content, str) and content:
        masked, b = await _scan(content, cfg, pool, audit, req_id, direction, ignore, block_types)
        msg["content"] = masked
        blocked = blocked or b
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]:
                masked, b = await _scan(part["text"], cfg, pool, audit, req_id, direction,
                                        ignore, block_types)
                part["text"] = masked
                blocked = blocked or b
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str) and fn["arguments"]:
            masked, b = await _scan(fn["arguments"], cfg, pool, audit, req_id, direction,
                                    ignore, block_types)
            fn["arguments"] = masked
            blocked = blocked or b
    # 레거시 단수 function_call(assistant) — deprecated이나 일부 클라이언트가 여전히 사용.
    fc = msg.get("function_call")
    if isinstance(fc, dict) and isinstance(fc.get("arguments"), str) and fc["arguments"]:
        masked, b = await _scan(fc["arguments"], cfg, pool, audit, req_id, direction,
                                ignore, block_types)
        fc["arguments"] = masked
        blocked = blocked or b
    return blocked


async def _check_in(messages: list[dict], cfg: PiiConfig, pool: NerPool | None,
                    audit: AuditLogger, req_id: str, ignore: frozenset[str]) -> bool:
    """요청 messages의 텍스트(평문·멀티모달 text·tool_calls)를 마스킹. 차단 여부 반환."""
    blocked = False
    for msg in messages:
        if isinstance(msg, dict):
            b = await _mask_message_texts(msg, cfg, pool, audit, req_id, "in",
                                          ignore, cfg.block_types)
            blocked = blocked or b
    return blocked


async def _mask_response_json(data: dict, cfg: PiiConfig, pool: NerPool | None,
                              audit: AuditLogger, req_id: str, ignore: frozenset[str]) -> dict:
    """비스트림 응답 choices[].message의 텍스트(content/reasoning/멀티모달/tool_calls)를 마스킹."""
    for ch in data.get("choices", []):
        msg = ch.get("message")
        if not isinstance(msg, dict):
            continue
        # reasoning 계열은 직접, content/멀티모달/tool_calls는 _mask_message_texts로.
        for field in ("reasoning", "reasoning_content"):
            v = msg.get(field)
            if isinstance(v, str) and v:
                masked, _ = await _scan(v, cfg, pool, audit, req_id, "out", ignore, [])
                msg[field] = masked
        await _mask_message_texts(msg, cfg, pool, audit, req_id, "out", ignore, [])
    return data


async def _proxy_nonstream(url, body, headers, cfg, pool, audit, req_id, client, ignore, bypass=False):
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
    if cfg.out_enabled and not bypass:
        try:
            data = await _mask_response_json(data, cfg, pool, audit, req_id, ignore)
        except NerUnavailable:
            if cfg.fail_mode == "closed":
                return JSONResponse({"error": "PII 검사 불가(응답 보류)"}, status_code=502)
            # fail-open: NER 없이 구조화 regex만이라도 적용(주민/카드 누출 방지).
            data = await _mask_response_json(data, cfg, None, audit, req_id, ignore)
    return JSONResponse(data, status_code=200)


_META_KEYS = ("id", "object", "created", "model", "system_fingerprint")


def _accumulate_stream(chunks: list[dict]) -> dict:
    """SSE 청크들을 의미 필드별로 누적한다(content/reasoning/tool_calls 분리 + 메타/usage 보존).

    post 모드가 전체를 한 덩어리로 합쳐 재방출할 때, OpenAI 호환 구조(id/created/usage/
    finish_reason, reasoning↔content 분리, tool_call 인자 재조립)를 잃지 않도록 한다.
    """
    content: list[str] = []
    reasoning: list[str] = []
    reasoning_content: list[str] = []
    tool_calls: dict[int, dict] = {}
    meta: dict = {}
    usage = None
    finish_reason = None
    role = "assistant"
    for c in chunks:
        for k in _META_KEYS:
            if c.get(k) is not None and k not in meta:
                meta[k] = c[k]
        if c.get("usage") is not None:
            usage = c["usage"]
        for ch in c.get("choices", []):
            if ch.get("finish_reason"):
                finish_reason = ch["finish_reason"]
            delta = ch.get("delta") or {}
            if isinstance(delta.get("role"), str):
                role = delta["role"]
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            if isinstance(delta.get("reasoning"), str):
                reasoning.append(delta["reasoning"])
            if isinstance(delta.get("reasoning_content"), str):
                reasoning_content.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"index": idx, "type": "function",
                          "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                if tc.get("type"):
                    slot["type"] = tc["type"]
                fn = tc.get("function") or {}
                if isinstance(fn.get("name"), str):
                    slot["function"]["name"] += fn["name"]
                if isinstance(fn.get("arguments"), str):
                    slot["function"]["arguments"] += fn["arguments"]
    return {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "reasoning_content": "".join(reasoning_content),
        "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
        "meta": meta, "usage": usage,
        "finish_reason": finish_reason or "stop", "role": role,
    }


async def _proxy_stream(url, body, headers, cfg, pool, audit, req_id, client, ignore, bypass=False):
    """스트리밍 프록시.

    stream_mode='post'(기본): 게이트웨이 SSE를 끝까지 모아 out 검사 → 마스킹 후 재방출.
        호환성을 위해 content/reasoning/tool_calls를 분리 마스킹하고 id/created/usage/
        finish_reason 메타를 보존한다(토큰 점진성만 포기 — buffer 점진 모드는 후속).
    stream_mode='off' 또는 bypass: 검사 없이 원문 SSE 패스스루.
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

    if not cfg.out_enabled or cfg.stream_mode == "off" or bypass:
        async def _passthrough() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()
        return StreamingResponse(_passthrough(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # stream_mode='post': 완결 후 1회 검사 → 마스킹 텍스트를 구조 보존하며 재방출
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

    acc = _accumulate_stream(chunks)

    async def _mask_all(p: NerPool | None) -> tuple[str, str, str]:
        c = (await _scan(acc["content"], cfg, p, audit, req_id, "out", ignore, []))[0] if acc["content"] else ""
        r = (await _scan(acc["reasoning"], cfg, p, audit, req_id, "out", ignore, []))[0] if acc["reasoning"] else ""
        rc = (await _scan(acc["reasoning_content"], cfg, p, audit, req_id, "out", ignore, []))[0] if acc["reasoning_content"] else ""
        for tc in acc["tool_calls"]:
            args = tc["function"]["arguments"]
            if args:
                tc["function"]["arguments"] = (await _scan(args, cfg, p, audit, req_id, "out", ignore, []))[0]
        return c, r, rc

    try:
        content, reasoning, reasoning_content = await _mask_all(pool)
    except NerUnavailable:
        if cfg.fail_mode == "closed":
            content, reasoning, reasoning_content = "[응답이 PII 검사 불가로 보류되었습니다]", "", ""
            acc["tool_calls"] = []
        else:
            # fail-open: NER 없이 구조화 regex만 적용
            content, reasoning, reasoning_content = await _mask_all(None)

    async def _emit() -> AsyncGenerator[bytes, None]:
        delta: dict = {"role": acc["role"], "content": content}
        if reasoning:
            delta["reasoning"] = reasoning
        if reasoning_content:
            delta["reasoning_content"] = reasoning_content
        if acc["tool_calls"]:
            delta["tool_calls"] = acc["tool_calls"]
        head = {**acc["meta"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        yield f"data: {json.dumps(head, ensure_ascii=False)}\n\n".encode()
        tail = {**acc["meta"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": acc["finish_reason"]}]}
        if acc["usage"] is not None:
            tail["usage"] = acc["usage"]
        yield f"data: {json.dumps(tail, ensure_ascii=False)}\n\n".encode()
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
