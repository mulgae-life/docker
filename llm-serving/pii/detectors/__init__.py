"""PII 탐지기 패키지 — 공개 API.

외부(프록시/훅)는 이 패키지의 심볼만 사용하고 내부 구현 모듈에 직접 접근하지 않는다.
"""
from .normalize import normalize_text
from .structured import (
    SENSITIVE_TYPES,
    PiiSpan,
    brn_checksum,
    detect,
    has_sensitive,
    luhn,
    mask,
    rrn_checksum,
)

__all__ = [
    "normalize_text",
    "PiiSpan",
    "detect",
    "mask",
    "has_sensitive",
    "rrn_checksum",
    "brn_checksum",
    "luhn",
    "SENSITIVE_TYPES",
]
