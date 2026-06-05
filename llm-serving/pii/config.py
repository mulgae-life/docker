"""PII 프록시/엔진 설정 (Pydantic) — 하드코딩 대신 yaml + env, 안전한 기본값.

게이트웨이(`vllm_gateway.py`)의 Pydantic 설정 패턴과 일관되게 맞춘다.
"""
from __future__ import annotations

import os
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class NerBackend(BaseModel):
    """NER 추론 서버 1대 (LB 풀의 원소)."""

    host: str = "127.0.0.1"
    port: int
    model_tag: str = ""  # 예: "townboy-kpfbert-kdpii" (관측/로그용)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class PiiConfig(BaseModel):
    """PII 프록시 전체 설정."""

    # ── 프록시 바인딩 (외부 5015 인수) ──
    host: str = "0.0.0.0"
    port: int = 5015

    # ── upstream 게이트웨이 (내부로 한 칸 이동) ──
    upstream_url: str = "http://127.0.0.1:6015"

    # ── NER LB 풀 ──
    ner_backends: list[NerBackend] = Field(default_factory=list)

    # ── 검사 토글 (in=프롬프트, out=응답) ──
    in_enabled: bool = True
    out_enabled: bool = True

    # ── 장애 모드: PII 엔진 불가 시 동작 ──
    # closed: 차단(누출 방지, 컴플라이언스 기본) / open: 통과(가용성, 명시 opt-in)
    # open이라도 구조화 regex(주민/카드)는 항상 적용된다(누출 방지).
    # Literal로 오타 시 기동 단계에서 fail-fast(잘못된 값이 조용히 fail-open으로 강등되는 것 방지).
    fail_mode: Literal["closed", "open"] = "closed"

    # ── 전면 우회(bypass) 허용 여부 ──
    # True면 요청 헤더 X-PII-Mode: bypass 로 PII 검사를 통째 건너뛸 수 있다(우회는 감사 기록).
    # 기본 True(우회 허용) — 강제(enforce)로 묶으려면 False로.
    # 토큰 미설정 시 헤더 하나로 우회되니 외부 포트는 주의.
    allow_bypass: bool = True

    # ── 우회 토큰(선택) ──
    # 비어있지 않으면, bypass에 헤더 X-PII-Bypass-Token 값이 이 토큰과 일치해야 한다.
    # 외부 포트가 열린 환경에서 '헤더 하나로 우회'를 막는 2차 가드(내부망이면 비워둬도 됨).
    # 시크릿이므로 env(PII_BYPASS_TOKEN)로 주입 권장(yaml 하드코딩 지양).
    bypass_token: str = Field(default_factory=lambda: os.environ.get("PII_BYPASS_TOKEN", ""))

    # ── NER 부분 장애 정책 ──
    # True: NER 모델 그룹 중 하나라도 실패하면 fail-closed(커버리지 손실 방지, 컴플라이언스).
    # False: 살아있는 모델의 union으로 계속(가용성). 부분 실패는 항상 로그로 남긴다.
    ner_require_all_backends: bool = False

    # ── 스트리밍 out 검사 ──
    # post: 완결 후 1회 검사·재방출(구조 보존, 토큰 점진성 포기) / off: 미검사 패스스루.
    # 점진(progressive) 마스킹 모드는 PII 경계 누출 위험이 커 별도 설계 후 도입 예정(현재 미구현).
    # Literal로 오타 시 기동 단계 fail-fast(잘못된 값이 조용히 off로 처리돼 누출되는 것 방지).
    stream_mode: Literal["post", "off"] = "post"

    # ── 정책: 차단 타입(그 외는 마스킹) ──
    block_types: list[str] = Field(default_factory=lambda: ["rrn", "card"])

    # ── 이미지(멀티모달) 정책 ──
    # 이미지 바이트 자체는 PII 검사가 불가능하다(텍스트 파트만 검사). PII 민감 배포에서
    # allow: 이미지 허용(텍스트만 검사) / block: 이미지 포함 요청을 422로 차단(이미지 PII 누출 차단).
    image_policy: Literal["allow", "block"] = "allow"

    # ── 서비스별 마스킹 토글 화이트리스트 ──
    # 요청 헤더 X-PII-Ignore-Types로 서비스가 '마스킹을 끌 수 있는' 타입 목록.
    # 조직명(org)은 PII가 아니라 문서 메타데이터라 서비스 재량으로 노출 허용한다.
    # 핵심 PII(이름/주소/주민/카드/전화 등)는 절대 넣지 않는다 → 헤더로도 못 끈다.
    # block_types와 겹쳐도 차단 타입은 토글 불가(프록시에서 차집합 적용).
    ignorable_types: list[str] = Field(default_factory=lambda: ["org"])

    # ── 타임아웃(초) ──
    ner_connect_timeout: float = 1.0
    ner_read_timeout: float = 3.0
    upstream_timeout: float = 300.0

    # ── 감사로그 ──
    # salt는 env(PII_AUDIT_SALT)로 주입(하드코딩 금지). 미설정 시 지문은 'NOSALT'.
    audit_salt: str = Field(default_factory=lambda: os.environ.get("PII_AUDIT_SALT", ""))
    audit_log_path: str = "logs/pii_audit.log"

    @classmethod
    def from_yaml(cls, path: str) -> "PiiConfig":
        """yaml 설정 파일을 로드한다(없는 키는 기본값). env가 yaml보다 우선(salt)."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg = cls(**data)
        # env salt가 있으면 yaml 값을 덮어쓴다(시크릿은 env 우선).
        env_salt = os.environ.get("PII_AUDIT_SALT")
        if env_salt:
            cfg = cfg.model_copy(update={"audit_salt": env_salt})
        return cfg
