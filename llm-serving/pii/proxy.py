"""PII 프록시 — 외부 :5015 인수, in/out 양방향 PII 검사 후 게이트웨이로 forward.

토폴로지: 클라이언트 → (이 프록시 :5015) → 게이트웨이(127.0.0.1:6015) → vLLM.
- in : messages 텍스트(평문 content + 멀티모달 text 파트 + tool_calls.arguments)를 검사.
       고유식별정보(주민/카드 등) 검출 시 차단(422), 그 외(이름/주소/조직/전화 등)는 마스킹 후 forward.
       ※ 이미지 바이트 자체는 검사 불가(설계상 한계) — 텍스트 파트만 검사.
- out: 응답 content/reasoning/멀티모달 text/tool_calls.arguments 를 마스킹.
       스트리밍은 stream_mode(post=완결 후 1회 / off=패스스루)로 처리.
       post는 다중 choices와 구조(id/created/usage/finish_reason, reasoning↔content 분리)를
       보존해 재방출하고, 검사 실패 시 가짜 SSE가 아니라 HTTP 503을 반환한다.
- bypass: allow_bypass=true + 헤더 X-PII-Mode:bypass 면 in/out 검사 통째 생략(감사 기록).
- fail-closed: NER 풀 전체 장애 시 차단(누출 방지). fail-open이라도 구조화 regex는 항상 적용.

기동: cd pii && python proxy.py -c configs/proxy.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from audit import AuditLogger
from config import PiiConfig
from detectors.ner_client import NerPool, NerUnavailable
from hooks import analyze

_log = logging.getLogger("pii.proxy")


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


def _has_image_part(messages: list) -> bool:
    """messages content 배열에 이미지(image_url 등) 파트가 있는지. 이미지 정책 판정용."""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    t = part.get("type", "")
                    if isinstance(t, str) and ("image" in t):  # image_url / input_image 등
                        return True
    return False


def _pii_mode(request: Request, cfg: PiiConfig) -> str:
    """요청 헤더 X-PII-Mode로 전면 우회 여부 결정. 'enforce'(기본) | 'bypass'.

    bypass는 cfg.allow_bypass=True일 때만 유효하다(운영자 명시 opt-in). 미허용 환경에서
    헤더가 와도 enforce로 강제해 '헤더 하나로 우회'를 막는다.
    """
    if not cfg.allow_bypass:
        return "enforce"
    mode = request.headers.get("x-pii-mode", "").strip().lower()
    if mode != "bypass":
        return "enforce"
    # 토큰이 설정돼 있으면 일치해야만 우회(외부 노출 시 '헤더 하나로 우회' 2차 가드).
    if cfg.bypass_token:
        token = request.headers.get("x-pii-bypass-token", "")
        if token != cfg.bypass_token:
            return "enforce"
    return "bypass"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg: PiiConfig = app.state.cfg
    # 오설정 방지: 외부 포트에 우회를 켜고(allow_bypass) 토큰이 비면 '헤더 하나로 전체 PII
    # 우회'가 된다. 내부망 단순우회 의도일 수 있어 강제하진 않되, 운영자가 인지하도록 경고.
    if cfg.allow_bypass and not cfg.bypass_token:
        _log.warning(
            "allow_bypass=true 이면서 bypass_token 미설정 — X-PII-Mode: bypass 헤더만으로 "
            "전체 PII 검사가 우회됩니다. 외부 포트 노출 시 PII_BYPASS_TOKEN 설정을 권장합니다.")
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

        # ── 이미지 정책 ── (bypass가 아니고 block 정책이면, 검사 불가한 이미지 포함 요청 차단)
        if (not bypass and cfg.image_policy == "block"
                and isinstance(payload.get("messages"), list)
                and _has_image_part(payload["messages"])):
            audit.record(request_id=req_id, direction="in", entity_type="image",
                         action="block", value="", decision_source="policy")
            return JSONResponse(
                {"error": {"message": "이미지 콘텐츠는 PII 검사가 불가하여 정책상 차단되었습니다.",
                           "type": "pii_image_blocked", "request_id": req_id}},
                status_code=422)

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
    """SSE 청크들을 choices index별로 누적한다(n>1 선택지 보존 + 메타/usage 보존).

    post 모드가 전체를 모아 재방출할 때 OpenAI 호환 구조(id/created/usage/finish_reason,
    reasoning↔content 분리, tool_call 인자 재조립, 다중 choices)를 잃지 않도록 한다.
    """
    choices: dict[int, dict] = {}
    meta: dict = {}
    usage = None

    def _slot(idx: int) -> dict:
        return choices.setdefault(idx, {
            "index": idx, "role": "assistant", "content": [], "reasoning": [],
            "reasoning_content": [], "tool_calls": {}, "finish_reason": None,
        })

    for c in chunks:
        for k in _META_KEYS:
            if c.get(k) is not None and k not in meta:
                meta[k] = c[k]
        if c.get("usage") is not None:
            usage = c["usage"]
        for ch in c.get("choices", []):
            s = _slot(ch.get("index", 0))
            if ch.get("finish_reason"):
                s["finish_reason"] = ch["finish_reason"]
            delta = ch.get("delta") or {}
            if isinstance(delta.get("role"), str):
                s["role"] = delta["role"]
            if isinstance(delta.get("content"), str):
                s["content"].append(delta["content"])
            if isinstance(delta.get("reasoning"), str):
                s["reasoning"].append(delta["reasoning"])
            if isinstance(delta.get("reasoning_content"), str):
                s["reasoning_content"].append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                tidx = tc.get("index", 0)
                tslot = s["tool_calls"].setdefault(
                    tidx, {"index": tidx, "type": "function",
                           "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    tslot["id"] = tc["id"]
                if tc.get("type"):
                    tslot["type"] = tc["type"]
                fn = tc.get("function") or {}
                if isinstance(fn.get("name"), str):
                    tslot["function"]["name"] += fn["name"]
                if isinstance(fn.get("arguments"), str):
                    tslot["function"]["arguments"] += fn["arguments"]

    out_choices = []
    for idx in sorted(choices):
        s = choices[idx]
        out_choices.append({
            "index": idx, "role": s["role"],
            "content": "".join(s["content"]),
            "reasoning": "".join(s["reasoning"]),
            "reasoning_content": "".join(s["reasoning_content"]),
            "tool_calls": [s["tool_calls"][i] for i in sorted(s["tool_calls"])],
            "finish_reason": s["finish_reason"] or "stop",
        })
    return {"choices": out_choices, "meta": meta, "usage": usage}


async def _mask_stream_choices(acc: dict, cfg: PiiConfig, pool: NerPool | None,
                               audit: AuditLogger, req_id: str, ignore: frozenset[str]) -> None:
    """누적된 각 choice의 텍스트(content/reasoning/tool_calls)를 제자리 마스킹한다."""
    for chx in acc["choices"]:
        for field in ("content", "reasoning", "reasoning_content"):
            if chx[field]:
                chx[field] = (await _scan(chx[field], cfg, pool, audit, req_id, "out", ignore, []))[0]
        for tc in chx["tool_calls"]:
            args = tc["function"]["arguments"]
            if args:
                tc["function"]["arguments"] = (await _scan(args, cfg, pool, audit, req_id, "out", ignore, []))[0]


async def _proxy_stream(url, body, headers, cfg, pool, audit, req_id, client, ignore, bypass=False):
    """스트리밍 프록시.

    stream_mode='post'(기본): 게이트웨이 SSE를 끝까지 모아 out 검사 → 마스킹 후 재방출.
        호환성을 위해 다중 choices·content/reasoning/tool_calls를 분리 마스킹하고
        id/created/usage/finish_reason 메타를 보존한다(토큰 점진성만 포기).
    stream_mode='off' 또는 bypass: 검사 없이 원문 SSE 패스스루.

    업스트림 장애는 단계(연결·에러본문·스트리밍 read) 무관하게 일관 매핑한다 —
    타임아웃→504, 그 외 연결오류→502. post 모드는 버퍼링 중이라 클라이언트에 가짜 SSE를
    흘리지 않고 깔끔히 HTTP 오류로 끝낼 수 있다.
    """
    def _timeout_resp() -> JSONResponse:
        return JSONResponse(
            {"error": {"message": "업스트림 타임아웃", "type": "upstream_timeout",
                       "request_id": req_id}}, status_code=504)

    def _error_resp() -> JSONResponse:
        return JSONResponse(
            {"error": {"message": "업스트림 연결 오류", "type": "upstream_error",
                       "request_id": req_id}}, status_code=502)

    try:
        req = client.build_request("POST", url, content=body, headers=headers)
        resp = await client.send(req, stream=True)
    except httpx.TimeoutException:
        return _timeout_resp()
    except httpx.HTTPError:
        return _error_resp()

    if resp.status_code != 200:
        try:
            eb = await resp.aread()
        except httpx.TimeoutException:
            await resp.aclose()
            return _timeout_resp()
        except httpx.HTTPError:
            await resp.aclose()
            return _error_resp()
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

    # stream_mode='post': 완결 후 1회 검사 → 마스킹 텍스트를 구조 보존하며 재방출.
    # 버퍼링 중(클라이언트에 미전송)이라 read 도중 장애도 가짜 SSE가 아니라 504/502로 매핑.
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
    except httpx.TimeoutException:
        return _timeout_resp()
    except httpx.HTTPError:
        return _error_resp()
    finally:
        await resp.aclose()

    acc = _accumulate_stream(chunks)

    # 아직 클라이언트에 아무것도 쓰지 않은 상태 → 검사 실패는 가짜 성공 SSE가 아니라 HTTP 오류로.
    try:
        await _mask_stream_choices(acc, cfg, pool, audit, req_id, ignore)
    except NerUnavailable:
        if cfg.fail_mode == "closed":
            return JSONResponse(
                {"error": {"message": "응답 PII 검사 일시 불가(보류)", "type": "pii_unavailable",
                           "request_id": req_id}},
                status_code=503)
        await _mask_stream_choices(acc, cfg, None, audit, req_id, ignore)  # fail-open: 구조화만

    async def _emit() -> AsyncGenerator[bytes, None]:
        head_choices = []
        for chx in acc["choices"]:
            delta: dict = {"role": chx["role"], "content": chx["content"]}
            if chx["reasoning"]:
                delta["reasoning"] = chx["reasoning"]
            if chx["reasoning_content"]:
                delta["reasoning_content"] = chx["reasoning_content"]
            if chx["tool_calls"]:
                delta["tool_calls"] = chx["tool_calls"]
            head_choices.append({"index": chx["index"], "delta": delta, "finish_reason": None})
        head = {**acc["meta"], "choices": head_choices or [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}
        yield f"data: {json.dumps(head, ensure_ascii=False)}\n\n".encode()
        tail_choices = [{"index": chx["index"], "delta": {}, "finish_reason": chx["finish_reason"]}
                        for chx in acc["choices"]] or [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        tail = {**acc["meta"], "choices": tail_choices}
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
