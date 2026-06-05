"""구조화 PII 탐지기 단위 테스트.

검증 워크플로우의 적대적 지적(전각/구분자 우회, 2020.10 주민번호 체크섬 무효,
겹침 충돌)을 회귀 테스트로 고정한다. 목표 기반: 무효 입력 케이스를 먼저 작성한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# pii/ 를 import 경로에 추가 (cd pii && pytest 외의 실행 위치도 허용)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detectors import (  # noqa: E402
    detect,
    has_sensitive,
    mask,
    normalize_text,
)
from detectors.structured import brn_checksum, luhn, rrn_checksum  # noqa: E402


# ── 체크섬 통과 테스트값 생성 헬퍼 ──────────────────────────
def _valid_rrn() -> str:
    """체크섬이 통과하는 13자리 주민번호(가공값)를 생성."""
    body = "900101123456"  # 앞 12자리(임의)
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    s = sum(int(d) * w for d, w in zip(body, weights))
    return body + str((11 - (s % 11)) % 10)


def _valid_brn() -> str:
    """체크섬이 통과하는 10자리 사업자등록번호(가공값)를 생성."""
    body = "123456789"
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    s = sum(int(d) * w for d, w in zip(body, weights)) + (int(body[8]) * 5) // 10
    return body + str((10 - (s % 10)) % 10)


_VALID_CARD = "4111111111111111"  # Luhn 통과(테스트용 Visa 번호)


# ── 체크섬 함수 ────────────────────────────────────────────
def test_checksum_functions():
    assert rrn_checksum(_valid_rrn()) is True
    assert rrn_checksum("9001011234567") is False  # 임의값(체크섬 불일치)
    assert brn_checksum(_valid_brn()) is True
    assert brn_checksum("1234567890") is False
    assert luhn(_VALID_CARD) is True
    assert luhn("4111111111111112") is False


# ── 주민등록번호 ───────────────────────────────────────────
def test_rrn_valid_checksum():
    rrn = _valid_rrn()
    text = f"제 주민번호는 {rrn[:6]}-{rrn[6:]}입니다."
    spans = detect(normalize_text(text))
    rrns = [s for s in spans if s.type == "rrn"]
    assert len(rrns) == 1
    assert rrns[0].checksum_valid is True


def test_rrn_invalid_checksum_still_detected():
    """2020.10 이후 임의화로 체크섬이 무효여도 '형식'으로 탐지되어야 한다."""
    text = "900101-1234567 로 처리해줘"  # 체크섬 불일치
    spans = detect(normalize_text(text))
    rrns = [s for s in spans if s.type == "rrn"]
    assert len(rrns) == 1
    assert rrns[0].checksum_valid is False  # 검출은 되되 체크섬은 실패 표기


def test_rrn_fullwidth_bypass_blocked():
    """전각 숫자 우회는 normalize(NFKC) 후 탐지되어야 한다."""
    text = "주민번호 ９００１０１-１２３４５６７"  # 전각
    assert detect(text) == [] or all(s.type != "rrn" for s in detect(text))  # 정규화 전엔 미탐
    spans = detect(normalize_text(text))
    assert any(s.type == "rrn" for s in spans)


def test_rrn_space_separator():
    text = "900101 1234567"
    spans = detect(normalize_text(text))
    assert any(s.type == "rrn" for s in spans)


# ── 카드 / 사업자 ──────────────────────────────────────────
def test_card_luhn():
    text = "카드 4111-1111-1111-1111 결제"
    spans = detect(normalize_text(text))
    cards = [s for s in spans if s.type == "card"]
    assert len(cards) == 1
    assert cards[0].checksum_valid is True


def test_card_space_separator():
    text = "4111 1111 1111 1111"
    spans = detect(normalize_text(text))
    assert any(s.type == "card" for s in spans)


def test_card_dot_separator_detected():
    """점(.) 구분 카드번호도 검출(결정적 우회 차단)."""
    spans = detect(normalize_text("카드 4111.1111.1111.1111 결제"))
    assert any(s.type == "card" for s in spans)


def test_rrn_dot_separator_detected():
    """점(.) 구분 주민번호도 검출."""
    spans = detect(normalize_text("주민 900101.1234567"))
    assert any(s.type == "rrn" for s in spans)


def test_dot_no_false_positive_on_ip_version():
    """IP·버전 문자열은 card/rrn으로 오탐하지 않는다(4그룹×4자리 조건 불충족)."""
    assert detect(normalize_text("서버 192.168.0.1 점검")) == []
    assert detect(normalize_text("빌드 버전 1.2.3.4")) == []


def test_nonstandard_grouping_card_promoted_to_block():
    """비표준 그룹핑(4-6-6-2)으로 4-4-4-4를 피한 카드는 card로 승격돼 차단 대상이 된다."""
    spans = detect(normalize_text("카드 4111-111111-111111-11"))
    assert any(s.type == "card" for s in spans)
    assert has_sensitive(spans) is True  # account 강등으로 인한 차단 회피 방지


def test_account_not_promoted_when_short_or_non_luhn():
    """13자리 미만이거나 Luhn 불통과 계좌는 그대로 account(오탐 승격 방지)."""
    s1 = detect(normalize_text("계좌 110-234-567890 신한"))  # 12자리
    assert [s.type for s in s1] == ["account"]
    assert has_sensitive(s1) is False


def test_brn_valid():
    brn = _valid_brn()
    text = f"사업자등록번호 {brn[:3]}-{brn[3:5]}-{brn[5:]}"
    spans = detect(normalize_text(text))
    brns = [s for s in spans if s.type == "brn"]
    assert len(brns) == 1
    assert brns[0].checksum_valid is True


# ── 전화 / 이메일 ──────────────────────────────────────────
def test_phone_and_email():
    text = "연락처 010-1234-5678, 메일 hong.gildong@example.co.kr"
    spans = detect(normalize_text(text))
    types = {s.type for s in spans}
    assert "phone" in types
    assert "email" in types


# ── 겹침 해소 ──────────────────────────────────────────────
def test_card_not_misdetected_as_account():
    """16자리 카드가 계좌(2~6-2~6-1~6) 패턴으로 잘못 쪼개지지 않아야 한다."""
    text = "4111-1111-1111-1111"
    spans = detect(normalize_text(text))
    assert len(spans) == 1
    assert spans[0].type == "card"


# ── 마스킹 / 정책 ──────────────────────────────────────────
def test_mask_replaces_with_label():
    rrn = _valid_rrn()
    text = f"{rrn[:6]}-{rrn[6:]} 확인"
    spans = detect(normalize_text(text))
    masked = mask(normalize_text(text), spans)
    assert "[주민등록번호]" in masked
    assert rrn[6:] not in masked  # 원본 숫자 잔존 없음


def test_has_sensitive():
    assert has_sensitive(detect(normalize_text("900101-1234567"))) is True   # 주민=차단대상
    assert has_sensitive(detect(normalize_text("010-1234-5678"))) is False   # 전화=마스킹대상


def test_clean_text_no_false_positive():
    text = "오늘 날씨가 좋네요. 보험 상담 받고 싶습니다."
    assert detect(normalize_text(text)) == []
