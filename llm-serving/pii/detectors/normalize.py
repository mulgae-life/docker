"""입력 정규화 — 전각→반각(NFKC) 통일로 PII 우회 차단.

검사 전 정규화하지 않으면 전각 숫자('９００１０１') 같은 변형이 `\\d` 정규식을
통과해버린다(KISA 등에서 확인된 실제 회피 벡터). 검사 직전에 NFKC를 적용한다.
"""
from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """NFKC 정규화로 전각 영숫자/기호를 반각으로 통일한다.

    예) '９００１０１' → '900101', 전각 하이픈/공백/마침표 → 반각.
    한글 음절은 NFKC에서 변하지 않으므로 한국어 본문에는 안전하다.
    """
    return unicodedata.normalize("NFKC", text)
