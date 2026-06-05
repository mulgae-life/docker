"""PII 감사로그 — 평문 미저장. HMAC-SHA256 지문 + JSONL.

컴플라이언스상 '무엇을 언제 어떤 조치했는가'는 남기되, **원문 PII는 절대 저장하지
않는다**(저장하면 감사로그 자체가 유출원이 된다). 매칭 증빙이 필요하면 솔트 기반
HMAC 지문 앞 12자만 남긴다(단순 SHA256은 무지개표에 취약하므로 금지).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from logging.handlers import WatchedFileHandler


def fingerprint(value: str, salt: str) -> str:
    """원문 대신 HMAC-SHA256 지문(앞 12자)을 반환. salt 미설정 시 'NOSALT'."""
    if not salt:
        return "NOSALT"
    return hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()[:12]


@dataclass(frozen=True)
class AuditEvent:
    """감사 이벤트 1건 (원문 PII 미포함)."""

    ts: str
    request_id: str
    direction: str        # "in" | "out"
    entity_type: str      # rrn, card, phone, person, address, ...
    action: str           # "block" | "mask" | "pass"
    fingerprint: str      # HMAC 지문(앞 12자) 또는 'NOSALT'
    decision_source: str  # "regex+checksum" | "ner"


class AuditLogger:
    """JSONL 감사 로거. 게이트웨이 로그와 분리된 전용 파일에 append."""

    def __init__(self, log_path: str, salt: str) -> None:
        self._salt = salt
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        self._logger = logging.getLogger("pii.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False  # 루트로 전파 금지(본문 로깅 경로 차단)
        if not self._logger.handlers:
            handler = WatchedFileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def record(
        self,
        *,
        request_id: str,
        direction: str,
        entity_type: str,
        action: str,
        value: str,
        decision_source: str,
    ) -> None:
        """이벤트 1건 기록. `value`는 지문 계산에만 쓰고 절대 저장하지 않는다."""
        event = AuditEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            request_id=request_id,
            direction=direction,
            entity_type=entity_type,
            action=action,
            fingerprint=fingerprint(value, self._salt),
            decision_source=decision_source,
        )
        self._logger.info(json.dumps(asdict(event), ensure_ascii=False))
