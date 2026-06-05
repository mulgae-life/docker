"""프록시 in/out 검사 흐름 통합 테스트.

실제 NER 서버/게이트웨이 없이 빈 NER 풀(구조화 regex만)로 차단/마스킹을 검증한다.
실서버 E2E(NER 풀 + 게이트웨이)는 `./start.sh up` 후 별도 HTTP 스모크로 확인.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from audit import AuditLogger  # noqa: E402
from config import PiiConfig  # noqa: E402
from detectors.ner_client import NerPool  # noqa: E402
from proxy import (  # noqa: E402
    _accumulate_stream,
    _check_in,
    _ignore_types,
    _mask_response_json,
    _pii_mode,
    _proxy_stream,
)


class _FakeReq:
    """_ignore_types용 최소 Request 스텁(.headers.get만 사용)."""
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _cfg() -> PiiConfig:
    return PiiConfig(ner_backends=[])  # NER 백엔드 없음 → 구조화 regex만


def _pool() -> NerPool:
    return NerPool(httpx.AsyncClient(), score_threshold=0.5)  # 빈 풀(detect→[])


def _audit() -> AuditLogger:
    d = tempfile.mkdtemp()
    return AuditLogger(f"{d}/audit.log", salt="testsalt")


def test_in_block_rrn():
    """주민번호(고유식별정보)는 차단 + 마스킹."""
    async def run():
        msgs = [{"role": "user", "content": "제 주민번호는 900101-1234567 입니다"}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "r1", frozenset())
        assert blocked is True
        assert "[주민등록번호]" in msgs[0]["content"]
    asyncio.run(run())


def test_in_mask_phone_not_blocked():
    """전화번호는 마스킹하되 차단하지 않음."""
    async def run():
        msgs = [{"role": "user", "content": "연락처 010-1234-5678 로 연락주세요"}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "r2", frozenset())
        assert blocked is False
        assert "[전화번호]" in msgs[0]["content"]
    asyncio.run(run())


def test_in_clean_passthrough():
    """PII 없는 입력은 원문 그대로 통과."""
    async def run():
        msgs = [{"role": "user", "content": "보험 상담 받고 싶어요"}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "r3", frozenset())
        assert blocked is False
        assert msgs[0]["content"] == "보험 상담 받고 싶어요"
    asyncio.run(run())


def test_out_mask_card_in_content():
    """응답 content의 카드번호 마스킹."""
    async def run():
        data = {"choices": [{"message": {"role": "assistant",
                "content": "확인된 카드번호는 4111-1111-1111-1111 입니다"}}]}
        out = await _mask_response_json(data, _cfg(), _pool(), _audit(), "r4", frozenset())
        assert "[신용카드번호]" in out["choices"][0]["message"]["content"]
    asyncio.run(run())


def test_out_mask_reasoning_field():
    """thinking(reasoning) 필드의 PII도 마스킹(검증 지적: content만 보면 누락)."""
    async def run():
        data = {"choices": [{"message": {"role": "assistant", "content": "네",
                "reasoning": "이메일 a@b.com 참고하세요"}}]}
        out = await _mask_response_json(data, _cfg(), _pool(), _audit(), "r5", frozenset())
        assert "[이메일]" in out["choices"][0]["message"]["reasoning"]
    asyncio.run(run())


def test_ignore_types_whitelist_and_block_guard():
    """X-PII-Ignore-Types: 화이트리스트(org)만 허용, 핵심/차단 타입은 토글 불가."""
    cfg = _cfg()  # 기본 ignorable_types=["org"], block_types=["rrn","card"]
    # 정상: org만 통과
    assert _ignore_types(_FakeReq({"x-pii-ignore-types": "org"}), cfg) == frozenset({"org"})
    # 화이트리스트 밖(person) 무시, org만 채택
    assert _ignore_types(_FakeReq({"x-pii-ignore-types": "org,person"}), cfg) == frozenset({"org"})
    # 차단 타입(rrn/card)은 화이트리스트에 없어 토글 불가 → 빈 집합
    assert _ignore_types(_FakeReq({"x-pii-ignore-types": "rrn,card"}), cfg) == frozenset()
    # 헤더 없음 → 빈 집합(전부 마스킹)
    assert _ignore_types(_FakeReq({}), cfg) == frozenset()


def test_ignore_block_type_even_if_misconfigured():
    """ignorable_types에 실수로 차단 타입을 넣어도 차집합 가드로 토글 불가."""
    cfg = PiiConfig(ner_backends=[], ignorable_types=["org", "rrn"])
    # rrn은 block_types와 겹쳐 차집합에서 제외 → org만 토글 가능
    assert _ignore_types(_FakeReq({"x-pii-ignore-types": "org,rrn"}), cfg) == frozenset({"org"})


# ── bypass 게이팅 (allow_bypass) ──
def test_invalid_fail_mode_rejected():
    """fail_mode/stream_mode 오타는 기동 단계에서 거부(조용한 fail-open 강등 방지)."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PiiConfig(ner_backends=[], fail_mode="close")     # 오타
    with pytest.raises(ValidationError):
        PiiConfig(ner_backends=[], stream_mode="postt")   # 오타
    # 정상 값은 통과
    assert PiiConfig(ner_backends=[], fail_mode="open").fail_mode == "open"


def test_pii_mode_bypass_requires_allow():
    """allow_bypass=False면 헤더가 와도 enforce(우회 불가)."""
    cfg_off = PiiConfig(ner_backends=[])  # allow_bypass 기본 False
    assert _pii_mode(_FakeReq({"x-pii-mode": "bypass"}), cfg_off) == "enforce"
    cfg_on = PiiConfig(ner_backends=[], allow_bypass=True)
    assert _pii_mode(_FakeReq({"x-pii-mode": "bypass"}), cfg_on) == "bypass"
    assert _pii_mode(_FakeReq({}), cfg_on) == "enforce"  # 헤더 없으면 기본 enforce


def test_pii_mode_bypass_token_required():
    """bypass_token이 설정되면 토큰 헤더가 일치해야만 우회."""
    cfg = PiiConfig(ner_backends=[], allow_bypass=True, bypass_token="s3cr3t")
    # 토큰 없음/불일치 → enforce
    assert _pii_mode(_FakeReq({"x-pii-mode": "bypass"}), cfg) == "enforce"
    assert _pii_mode(_FakeReq({"x-pii-mode": "bypass", "x-pii-bypass-token": "wrong"}), cfg) == "enforce"
    # 일치 → bypass
    assert _pii_mode(_FakeReq({"x-pii-mode": "bypass", "x-pii-bypass-token": "s3cr3t"}), cfg) == "bypass"


# ── P0: 멀티모달 content 배열의 text 파트 검사 ──
def test_in_multimodal_text_part_masked():
    """content가 배열이어도 {type:text} 파트의 PII는 마스킹된다(우회 차단)."""
    async def run():
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "내 번호 010-1234-5678"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "m1", frozenset())
        assert blocked is False
        assert "[전화번호]" in msgs[0]["content"][0]["text"]
        # 이미지 파트는 그대로 보존
        assert msgs[0]["content"][1]["type"] == "image_url"
    asyncio.run(run())


def test_in_multimodal_rrn_blocked():
    """멀티모달 text 파트의 주민번호도 차단 대상."""
    async def run():
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "주민번호 900101-1234567"},
        ]}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "m2", frozenset())
        assert blocked is True
        assert "[주민등록번호]" in msgs[0]["content"][0]["text"]
    asyncio.run(run())


# ── P1: tool_calls.arguments 검사 ──
def test_in_tool_call_arguments_masked():
    """assistant tool_calls의 함수 인자 JSON 안 PII도 마스킹."""
    async def run():
        msgs = [{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "send", "arguments": '{"email":"a@b.com"}'}},
        ]}]
        await _check_in(msgs, _cfg(), _pool(), _audit(), "t1", frozenset())
        assert "[이메일]" in msgs[0]["tool_calls"][0]["function"]["arguments"]
    asyncio.run(run())


def test_out_tool_call_arguments_masked():
    """응답 tool_calls 인자의 PII도 마스킹."""
    async def run():
        data = {"choices": [{"message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "pay", "arguments": '{"card":"4111-1111-1111-1111"}'}}]}}]}
        out = await _mask_response_json(data, _cfg(), _pool(), _audit(), "t2", frozenset())
        assert "[신용카드번호]" in out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    asyncio.run(run())


def test_in_legacy_function_call_masked():
    """레거시 단수 function_call.arguments의 PII도 검사(우회 방지)."""
    async def run():
        msgs = [{"role": "assistant", "content": "",
                 "function_call": {"name": "pay", "arguments": '{"card":"4111-1111-1111-1111"}'}}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "fc1", frozenset())
        assert blocked is True  # 카드=차단대상
        assert "[신용카드번호]" in msgs[0]["function_call"]["arguments"]
    asyncio.run(run())


def test_in_nonstandard_text_part_masked():
    """content 배열의 비표준 text 키(input_text 등)도 검사(우회 방지)."""
    async def run():
        msgs = [{"role": "user", "content": [
            {"type": "input_text", "text": "주민 900101-1234567"},
        ]}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "it1", frozenset())
        assert blocked is True
        assert "[주민등록번호]" in msgs[0]["content"][0]["text"]
    asyncio.run(run())


# ── P2: fail-open 시에도 구조화 regex 적용 (pool=None) ──
def test_failopen_structured_still_applied():
    """NER 없이(pool=None) 호출돼도 구조화 주민번호는 마스킹·차단된다."""
    async def run():
        msgs = [{"role": "user", "content": "주민 900101-1234567 카드 4111-1111-1111-1111"}]
        blocked = await _check_in(msgs, _cfg(), None, _audit(), "f1", frozenset())
        assert blocked is True
        assert "[주민등록번호]" in msgs[0]["content"]
        assert "[신용카드번호]" in msgs[0]["content"]
    asyncio.run(run())


# ── P1: 스트리밍 누적이 구조(content/reasoning/usage/tool_calls)를 보존 ──
def test_accumulate_stream_preserves_structure():
    chunks = [
        {"id": "x1", "created": 1, "model": "gemma", "object": "chat.completion.chunk",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": "안녕 "}}]},
        {"choices": [{"index": 0, "delta": {"reasoning": "생각1 "}}]},
        {"choices": [{"index": 0, "delta": {"content": "010-1234-5678"}}]},
        {"choices": [{"index": 0, "delta": {
            "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "f", "arguments": '{"x":'}}]}}]},
        {"choices": [{"index": 0, "delta": {
            "tool_calls": [{"index": 0, "function": {"arguments": '1}'}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"total_tokens": 7}},
    ]
    acc = _accumulate_stream(chunks)
    c0 = acc["choices"][0]
    assert c0["content"] == "안녕 010-1234-5678"
    assert c0["reasoning"] == "생각1 "           # content와 분리 보존
    assert acc["meta"]["id"] == "x1"             # 메타 보존
    assert acc["usage"] == {"total_tokens": 7}   # usage 보존
    assert c0["finish_reason"] == "stop"
    assert c0["tool_calls"][0]["function"]["arguments"] == '{"x":1}'  # 인자 재조립


def test_accumulate_stream_multiple_choices():
    """n>1: 여러 choices가 index별로 보존된다(하나로 합쳐 손상되지 않음)."""
    chunks = [
        {"id": "y1", "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": "첫째"}},
            {"index": 1, "delta": {"role": "assistant", "content": "둘째"}},
        ]},
        {"choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"},
            {"index": 1, "delta": {}, "finish_reason": "length"},
        ]},
    ]
    acc = _accumulate_stream(chunks)
    assert len(acc["choices"]) == 2
    assert acc["choices"][0]["content"] == "첫째" and acc["choices"][0]["finish_reason"] == "stop"
    assert acc["choices"][1]["content"] == "둘째" and acc["choices"][1]["finish_reason"] == "length"


class _RaisingResp:
    """200으로 시작했으나 read(aiter_lines) 도중 예외를 던지는 업스트림 응답 스텁."""
    status_code = 200

    def __init__(self, exc: Exception):
        self._exc = exc
        self.closed = False

    async def aiter_lines(self):
        raise self._exc
        yield  # 제너레이터로 만들기 위한 미도달 yield

    async def aclose(self):
        self.closed = True


class _FakeClient:
    """build_request/send만 흉내 — send는 미리 받은 resp를 반환."""
    def __init__(self, resp):
        self._resp = resp

    def build_request(self, *a, **k):
        return object()

    async def send(self, req, stream=True):
        return self._resp


def _run_stream(exc):
    cfg = PiiConfig(ner_backends=[], stream_mode="post")
    resp = _RaisingResp(exc)
    client = _FakeClient(resp)
    out = asyncio.run(_proxy_stream(
        "http://gw/v1/chat/completions", b"{}", {}, cfg,
        pool=None, audit=None, req_id="rid", client=client, ignore=frozenset()))
    return out, resp


def test_stream_read_timeout_maps_504():
    """post 모드 버퍼링 중 업스트림 타임아웃 → 가짜 SSE가 아니라 504로 매핑(자원 정리 포함)."""
    out, resp = _run_stream(httpx.ReadTimeout("boom"))
    assert out.status_code == 504
    assert resp.closed  # finally에서 aclose 호출됨


def test_stream_read_error_maps_502():
    """업스트림 연결 오류(non-timeout HTTPError) → 502로 매핑."""
    out, resp = _run_stream(httpx.RemoteProtocolError("reset"))
    assert out.status_code == 502
    assert resp.closed


class _SendRaisingClient:
    """send() 단계(연결 빌드)에서 예외를 던지는 클라이언트 스텁."""
    def __init__(self, exc: Exception):
        self._exc = exc

    def build_request(self, *a, **k):
        return object()

    async def send(self, req, stream=True):
        raise self._exc


def test_stream_send_timeout_maps_504():
    """연결 빌드 단계 타임아웃도 502가 아니라 504로 매핑(read loop와 일관)."""
    cfg = PiiConfig(ner_backends=[], stream_mode="post")
    out = asyncio.run(_proxy_stream(
        "http://gw/v1/chat/completions", b"{}", {}, cfg, pool=None, audit=None,
        req_id="rid", client=_SendRaisingClient(httpx.ConnectTimeout("t")), ignore=frozenset()))
    assert out.status_code == 504
