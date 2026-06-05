"""구조화 한국 PII 탐지 — regex + 체크섬 (결정적).

비정형 PII(이름/주소/조직)는 NER이 담당하고, 여기서는 포맷이 고정되고
체크섬으로 검증 가능한 식별번호만 다룬다. 결정적이라 재현·감사가 용이하고,
NER(확률 추론)보다 정밀도/재현율 모두 우위다.

설계 메모:
- 검사 전 `normalize.normalize_text`로 전각→반각 정규화를 선적용할 것(우회 차단).
- 체크섬은 '오탐 저감 보조'다. 특히 주민등록번호는 2020.10 이후 발급분이
  뒤 7자리 임의화로 체크섬이 무효이므로, 마스킹 판정은 형식 일치를 우선하고
  체크섬은 `checksum_valid` 플래그로만 표기한다(차단을 막지 않는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── PII 타입 ──────────────────────────────────────────────
# 차단 우선순위(겹침 해소용): 큰 값일수록 우선. 식별력 높은 번호가 우선한다.
_PRIORITY = {
    "rrn": 6,       # 주민등록번호
    "card": 5,      # 신용카드
    "brn": 4,       # 사업자등록번호
    "phone": 3,     # 전화(휴대폰 01X + 지역번호 02·0XX·070 → 광범위한 계좌 패턴보다 우선)
    "account": 2,   # 계좌번호 (은행별 상이, 컨텍스트 키워드 동반 권장)
    "email": 1,     # 이메일
}

_TYPE_KO = {
    "rrn": "주민등록번호",
    "card": "신용카드번호",
    "brn": "사업자등록번호",
    "account": "계좌번호",
    "phone": "전화번호",
    "email": "이메일",
}

# 고유식별정보(개인정보보호법) — 기본 정책상 '차단' 대상. 그 외는 '마스킹'.
SENSITIVE_TYPES = frozenset({"rrn", "card"})


@dataclass(frozen=True)
class PiiSpan:
    """탐지된 구조화 PII 1건."""

    type: str
    start: int
    end: int
    value: str
    checksum_valid: bool  # 체크섬 통과 여부(형식만 맞고 체크섬 실패면 False)

    @property
    def label_ko(self) -> str:
        return _TYPE_KO.get(self.type, self.type)


# ── 정규식 (구분자 하이픈/공백 허용; 전각은 normalize가 선처리) ──
# 음수 lookaround로 더 긴 숫자열의 일부를 잘못 끊지 않도록 한다.
_RRN_RE = re.compile(r"(?<!\d)(\d{6})[-\s]?([1-4]\d{6})(?!\d)")
_BRN_RE = re.compile(r"(?<!\d)(\d{3})[-\s]?(\d{2})[-\s]?(\d{5})(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(\d{4})[-\s]?(\d{4})[-\s]?(\d{4})[-\s]?(\d{4})(?!\d)")
# 휴대폰(01X) + 지역번호(02 서울 / 0XX 국번) + 인터넷전화(070). 모두 0으로 시작하므로
# 1로 시작하는 사업자(BRN)·계좌와 겹치지 않는다. 겹치면 _PRIORITY로 phone>account.
_PHONE_RE = re.compile(r"(?<!\d)(0\d{1,2})[-\s]?(\d{3,4})[-\s]?(\d{4})(?!\d)")
_ACCOUNT_RE = re.compile(r"(?<!\d)(\d{2,6})-(\d{2,6})-(\d{1,6})(?!\d)")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+(?![\w.-])")


# ── 체크섬 ────────────────────────────────────────────────
def rrn_checksum(digits13: str) -> bool:
    """주민등록번호 13자리 Mod-11 체크섬.

    ※ 2020.10 이후 발급분은 뒤 7자리 임의화로 이 체크섬이 무효다.
       따라서 '검증 통과 = 진짜'가 아니라 '검증 실패 ≠ 가짜'임에 유의(보조 용도).
    """
    if len(digits13) != 13 or not digits13.isdigit():
        return False
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    s = sum(int(d) * w for d, w in zip(digits13[:12], weights))
    check = (11 - (s % 11)) % 10
    return check == int(digits13[12])


def brn_checksum(digits10: str) -> bool:
    """사업자등록번호 10자리 Mod-10 체크섬 (국세청 규칙)."""
    if len(digits10) != 10 or not digits10.isdigit():
        return False
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    s = sum(int(d) * w for d, w in zip(digits10[:9], weights))
    s += (int(digits10[8]) * 5) // 10
    check = (10 - (s % 10)) % 10
    return check == int(digits10[9])


def luhn(digits: str) -> bool:
    """카드번호 Luhn(Mod-10) 체크섬."""
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# ── 탐지 ──────────────────────────────────────────────────
def detect(text: str) -> list[PiiSpan]:
    """구조화 PII span 목록을 반환한다.

    겹치는 span은 `_PRIORITY`가 높은 타입을 남기고 제거한다(카드 16자리가
    계좌/전화 패턴에 부분 매칭되는 충돌을 막기 위함).
    호출 측에서 `normalize_text`를 선적용하는 것을 권장한다.
    """
    raw: list[PiiSpan] = []

    for m in _RRN_RE.finditer(text):
        d = _digits(m.group())
        raw.append(PiiSpan("rrn", m.start(), m.end(), m.group(), rrn_checksum(d)))
    for m in _CARD_RE.finditer(text):
        d = _digits(m.group())
        raw.append(PiiSpan("card", m.start(), m.end(), m.group(), luhn(d)))
    for m in _BRN_RE.finditer(text):
        d = _digits(m.group())
        raw.append(PiiSpan("brn", m.start(), m.end(), m.group(), brn_checksum(d)))
    for m in _ACCOUNT_RE.finditer(text):
        raw.append(PiiSpan("account", m.start(), m.end(), m.group(), False))
    for m in _PHONE_RE.finditer(text):
        raw.append(PiiSpan("phone", m.start(), m.end(), m.group(), False))
    for m in _EMAIL_RE.finditer(text):
        raw.append(PiiSpan("email", m.start(), m.end(), m.group(), False))

    return _resolve_overlaps(raw)


def _resolve_overlaps(spans: list[PiiSpan]) -> list[PiiSpan]:
    """겹치는 span 중 우선순위가 높은 것만 남긴다."""
    # 우선순위 desc, 길이 desc 순으로 보며 이미 점유된 구간과 겹치면 버린다.
    ordered = sorted(spans, key=lambda s: (_PRIORITY[s.type], s.end - s.start), reverse=True)
    kept: list[PiiSpan] = []
    for sp in ordered:
        if any(not (sp.end <= k.start or sp.start >= k.end) for k in kept):
            continue
        kept.append(sp)
    return sorted(kept, key=lambda s: s.start)


# ── 마스킹 ────────────────────────────────────────────────
def mask(text: str, spans: list[PiiSpan]) -> str:
    """span을 `[라벨]`로 치환한다. 뒤에서부터 치환해 offset을 보존한다.

    부분 마스킹(끝자리 노출)은 재식별 위험이 있어 기본은 라벨 전체 치환이다.
    """
    for sp in sorted(spans, key=lambda s: s.start, reverse=True):
        text = text[: sp.start] + f"[{sp.label_ko}]" + text[sp.end :]
    return text


def has_sensitive(spans: list[PiiSpan]) -> bool:
    """차단 대상(고유식별정보: 주민/카드)이 포함됐는지."""
    return any(sp.type in SENSITIVE_TYPES for sp in spans)
