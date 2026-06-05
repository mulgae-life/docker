"""HTTP 레벨 E2E — 프록시 앱(ASGI) + upstream 게이트웨이 Mock.

프록시 라우트를 실제 HTTP로 호출하고, upstream(게이트웨이)은 httpx.MockTransport로
가로채 forward된 body를 캡처한다. 요청 차단/마스킹 → forward → 응답 마스킹의 전
경로를 GPU·실서버 없이 검증한다. (NER 풀은 빈 풀=구조화 regex만)
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from audit import AuditLogger  # noqa: E402
from config import PiiConfig  # noqa: E402
from detectors.ner_client import NerPool  # noqa: E402
from proxy import create_app  # noqa: E402


def _build(upstream_response: dict, **cfg_kwargs):
    """프록시 앱 + upstream Mock 구성. (app, captured) 반환 — captured['body']에 forward된 body."""
    captured: dict = {}
    cfg = PiiConfig(ner_backends=[], upstream_url="http://gateway:6015", **cfg_kwargs)

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=upstream_response)

    app = create_app(cfg)
    # lifespan 대신 state 수동 주입(ASGITransport는 lifespan 미실행)
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    app.state.pool = NerPool(httpx.AsyncClient())  # 빈 풀 → 구조화만
    app.state.audit = AuditLogger(f"{tempfile.mkdtemp()}/audit.log", salt="testsalt")
    return app, captured


async def _post(app, payload: dict, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as c:
        return await c.post("/v1/chat/completions", json=payload, headers=headers or {})


def test_e2e_block_rrn_not_forwarded():
    """주민번호 입력은 422 차단 + upstream으로 forward되지 않아야 한다."""
    async def run():
        app, captured = _build({"choices": [{"message": {"content": "ok"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": "주민번호 900101-1234567"}]})
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "pii_blocked"
        assert "body" not in captured  # 게이트웨이 미호출(추론·과금 0)
    asyncio.run(run())


def test_e2e_mask_forwarded():
    """전화번호는 마스킹된 채 게이트웨이로 forward + 200."""
    async def run():
        app, captured = _build({"choices": [{"message": {"role": "assistant", "content": "넵"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": "010-1234-5678 로 연락주세요"}]})
        assert r.status_code == 200
        forwarded = json.loads(captured["body"])
        assert "[전화번호]" in forwarded["messages"][0]["content"]
        assert "5678" not in forwarded["messages"][0]["content"]  # 원본 잔존 없음
    asyncio.run(run())


def test_e2e_out_response_masked():
    """게이트웨이 응답에 카드번호가 있으면 클라이언트에 마스킹돼 반환."""
    async def run():
        app, _ = _build({"choices": [{"message": {"role": "assistant",
                "content": "카드 4111-1111-1111-1111 확인했습니다"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": "확인해줘"}]})
        assert r.status_code == 200
        content = r.json()["choices"][0]["message"]["content"]
        assert "[신용카드번호]" in content
        assert "4111" not in content
    asyncio.run(run())


def test_e2e_clean_roundtrip():
    """PII 없는 정상 요청은 원문 forward + 응답 그대로."""
    async def run():
        app, captured = _build({"choices": [{"message": {"role": "assistant", "content": "서울입니다"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": "수도가 어디야"}]})
        assert r.status_code == 200
        assert json.loads(captured["body"])["messages"][0]["content"] == "수도가 어디야"
        assert r.json()["choices"][0]["message"]["content"] == "서울입니다"
    asyncio.run(run())


def test_e2e_multimodal_text_part_masked():
    """멀티모달 content 배열의 text 파트 전화번호가 마스킹된 채 forward (P0 우회 차단)."""
    async def run():
        app, captured = _build({"choices": [{"message": {"role": "assistant", "content": "네"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "여기로 010-1234-5678"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]})
        assert r.status_code == 200
        fwd = json.loads(captured["body"])["messages"][0]["content"]
        assert "[전화번호]" in fwd[0]["text"] and "5678" not in fwd[0]["text"]
        assert fwd[1]["type"] == "image_url"  # 이미지 파트 보존
    asyncio.run(run())


def test_e2e_image_policy_block():
    """image_policy=block이면 이미지 포함 요청을 422 차단(이미지 PII 미검사 누출 방지)."""
    async def run():
        app, captured = _build({"choices": [{"message": {"content": "ok"}}]}, image_policy="block")
        r = await _post(app, {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "이 사진 설명"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]})
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "pii_image_blocked"
        assert "body" not in captured  # 게이트웨이 미호출
    asyncio.run(run())


def test_e2e_image_policy_allow_passes():
    """image_policy=allow(기본)면 이미지 요청 통과(텍스트 파트만 검사)."""
    async def run():
        app, captured = _build({"choices": [{"message": {"role": "assistant", "content": "네"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "설명해줘"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]})
        assert r.status_code == 200
    asyncio.run(run())


def test_e2e_bypass_disabled_still_masks():
    """allow_bypass=False(기본)면 X-PII-Mode:bypass 헤더가 와도 마스킹 강제."""
    async def run():
        app, captured = _build({"choices": [{"message": {"role": "assistant", "content": "넵"}}]})
        r = await _post(app, {"messages": [{"role": "user", "content": "010-1234-5678"}]},
                        headers={"X-PII-Mode": "bypass"})
        assert r.status_code == 200
        assert "[전화번호]" in json.loads(captured["body"])["messages"][0]["content"]
    asyncio.run(run())


def test_e2e_bypass_enabled_forwards_raw():
    """allow_bypass=True + 헤더면 PII 검사 생략 → 원문 그대로 forward."""
    async def run():
        app, captured = _build({"choices": [{"message": {"role": "assistant", "content": "넵"}}]},
                               allow_bypass=True)
        r = await _post(app, {"messages": [{"role": "user", "content": "010-1234-5678 로 연락"}]},
                        headers={"X-PII-Mode": "bypass"})
        assert r.status_code == 200
        # 우회: 마스킹 안 됨(원문 유지)
        assert "010-1234-5678" in json.loads(captured["body"])["messages"][0]["content"]
    asyncio.run(run())


def test_e2e_bypass_enabled_skips_out_masking():
    """bypass면 응답 out 마스킹도 생략(원문 카드번호 그대로 반환)."""
    async def run():
        app, _ = _build({"choices": [{"message": {"role": "assistant",
                "content": "카드 4111-1111-1111-1111"}}]}, allow_bypass=True)
        r = await _post(app, {"messages": [{"role": "user", "content": "확인"}]},
                        headers={"X-PII-Mode": "bypass"})
        assert r.status_code == 200
        assert "4111-1111-1111-1111" in r.json()["choices"][0]["message"]["content"]
    asyncio.run(run())
