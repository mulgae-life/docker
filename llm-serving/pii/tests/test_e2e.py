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
from proxy import _check_in, _mask_response_json  # noqa: E402


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
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "r1")
        assert blocked is True
        assert "[주민등록번호]" in msgs[0]["content"]
    asyncio.run(run())


def test_in_mask_phone_not_blocked():
    """전화번호는 마스킹하되 차단하지 않음."""
    async def run():
        msgs = [{"role": "user", "content": "연락처 010-1234-5678 로 연락주세요"}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "r2")
        assert blocked is False
        assert "[전화번호]" in msgs[0]["content"]
    asyncio.run(run())


def test_in_clean_passthrough():
    """PII 없는 입력은 원문 그대로 통과."""
    async def run():
        msgs = [{"role": "user", "content": "보험 상담 받고 싶어요"}]
        blocked = await _check_in(msgs, _cfg(), _pool(), _audit(), "r3")
        assert blocked is False
        assert msgs[0]["content"] == "보험 상담 받고 싶어요"
    asyncio.run(run())


def test_out_mask_card_in_content():
    """응답 content의 카드번호 마스킹."""
    async def run():
        data = {"choices": [{"message": {"role": "assistant",
                "content": "확인된 카드번호는 4111-1111-1111-1111 입니다"}}]}
        out = await _mask_response_json(data, _cfg(), _pool(), _audit(), "r4")
        assert "[신용카드번호]" in out["choices"][0]["message"]["content"]
    asyncio.run(run())


def test_out_mask_reasoning_field():
    """thinking(reasoning) 필드의 PII도 마스킹(검증 지적: content만 보면 누락)."""
    async def run():
        data = {"choices": [{"message": {"role": "assistant", "content": "네",
                "reasoning": "이메일 a@b.com 참고하세요"}}]}
        out = await _mask_response_json(data, _cfg(), _pool(), _audit(), "r5")
        assert "[이메일]" in out["choices"][0]["message"]["reasoning"]
    asyncio.run(run())
