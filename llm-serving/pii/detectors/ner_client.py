"""NER LB 클라이언트 — 백엔드 풀 least-conn + label 통합 매핑 + union.

게이트웨이 `LoadBalancer`(active_connections 최소 선택) 패턴을 이식한다.
- 같은 모델 tag의 replica는 least-connection으로 분산(LB).
- 서로 다른 모델(vmaca/townboy)은 병렬 호출 후 union으로 span을 합친다(다층 방어).
- 모델별 raw 라벨(NAME / LC_ADDRESS / QT_CARD_NUMBER …)을 통합 PII 타입으로 정규화.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

_log = logging.getLogger("pii.ner")

# 모델 raw 라벨 → 통합 PII 타입. 비정형(person/address/org) + 정형 안전망(rrn/card/...).
_LABEL_MAP: dict[str, dict[str, str]] = {
    "vmaca123": {"NAME": "person", "ADDRESS": "address", "ORG": "org"},
    "townboy": {
        "PS_NAME": "person", "PS_NICKNAME": "person", "PS_ID": "person",
        "LC_ADDRESS": "address",  # LC_PLACE(일반 장소)·LCP_COUNTRY(국가)는 PII 아님 → 제외(과마스킹 방지)
        "OG_WORKPLACE": "org", "OG_DEPARTMENT": "org",
        "OGG_CLUB": "org", "OGG_EDUCATION": "org", "OGG_RELIGION": "org",
        "QT_RESIDENT_NUMBER": "rrn", "QT_CARD_NUMBER": "card",
        "QT_ACCOUNT_NUMBER": "account", "QT_MOBILE": "phone", "QT_PHONE": "phone",
        "QT_PASSPORT_NUMBER": "passport", "QT_ALIEN_NUMBER": "alien",
        "QT_DRIVER_NUMBER": "driver", "DT_BIRTH": "birth", "TMI_EMAIL": "email",
    },
}


class NerUnavailable(Exception):
    """NER 풀 전체가 응답 불가(fail-closed 트리거)."""


@dataclass(frozen=True)
class NerSpan:
    type: str       # 통합 PII 타입 (person/address/org/rrn/...)
    start: int
    end: int
    word: str
    score: float
    model: str      # 출처 모델 tag


@dataclass
class _Backend:
    host: str
    port: int
    model_tag: str
    active: int = 0
    healthy: bool = True

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class NerPool:
    """모델 tag별 replica 풀. tag 내 least-conn LB, tag 간 union."""

    def __init__(self, client: httpx.AsyncClient, *, score_threshold: float = 0.5,
                 require_all_backends: bool = False) -> None:
        self._client = client
        self._threshold = score_threshold
        # True면 모델 그룹 하나라도 실패 시 fail-closed(부분 커버리지 손실 방지).
        self._require_all = require_all_backends
        self._groups: dict[str, list[_Backend]] = {}
        self._lock = asyncio.Lock()

    def add_backend(self, host: str, port: int, model_tag: str) -> None:
        self._groups.setdefault(model_tag, []).append(_Backend(host, port, model_tag))

    async def _pick(self, group: list[_Backend]) -> _Backend | None:
        async with self._lock:
            healthy = [b for b in group if b.healthy]
            if not healthy:
                return None
            b = min(healthy, key=lambda x: x.active)
            b.active += 1
            return b

    async def _release(self, b: _Backend) -> None:
        async with self._lock:
            b.active = max(0, b.active - 1)

    async def _call_group(self, tag: str, group: list[_Backend], text: str,
                          connect_to: float, read_to: float) -> list[NerSpan]:
        b = await self._pick(group)
        if b is None:
            raise NerUnavailable(f"no healthy backend for {tag}")
        try:
            r = await self._client.post(
                f"{b.url}/ner", json={"text": text},
                timeout=httpx.Timeout(read_to, connect=connect_to),
            )
            r.raise_for_status()
            lmap = _LABEL_MAP.get(tag, {})
            spans: list[NerSpan] = []
            for e in r.json().get("entities", []):
                if e["score"] < self._threshold:
                    continue
                t = lmap.get(e["entity_group"])
                if not t:
                    continue
                spans.append(NerSpan(t, int(e["start"]), int(e["end"]),
                                     e["word"], float(e["score"]), tag))
            return spans
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            b.healthy = False  # health loop이 복구할 때까지 라우팅 제외
            raise NerUnavailable(f"{tag} 호출 실패: {exc}") from exc
        finally:
            await self._release(b)

    async def detect(self, text: str, *, connect_to: float, read_to: float) -> list[NerSpan]:
        """모델 그룹 병렬 호출 → union. 전 그룹 실패 시 NerUnavailable(fail-closed)."""
        if not self._groups:
            return []
        results = await asyncio.gather(
            *[self._call_group(tag, g, text, connect_to, read_to)
              for tag, g in self._groups.items()],
            return_exceptions=True,
        )
        spans: list[NerSpan] = []
        errors: list[BaseException] = []
        for r in results:
            if isinstance(r, BaseException):
                errors.append(r)
            else:
                spans.extend(r)
        # 전 그룹 실패면 항상 fail-closed.
        if errors and len(errors) == len(self._groups):
            raise NerUnavailable(f"NER 풀 전체 실패: {errors}")
        # 부분 실패(일부 그룹만 실패): 살아있는 그룹 커버리지가 누락된다(예: vmaca 다운→이름/주소/조직
        # 탐지 손실). 'silently' 통과를 막기 위해 항상 경고 로그를 남기고, require_all이면 fail-closed.
        if errors:
            failed = len(errors)
            total = len(self._groups)
            _log.warning("NER 부분 장애: %d/%d 그룹 실패 → 탐지 커버리지 저하 가능. errors=%s",
                         failed, total, errors)
            if self._require_all:
                raise NerUnavailable(f"NER 부분 장애(require_all): {failed}/{total} 그룹 실패")
        return spans

    async def health_check(self) -> None:
        """모든 백엔드 /health 폴링 → healthy 갱신(프록시 lifespan에서 주기 호출)."""
        for group in self._groups.values():
            for b in group:
                try:
                    r = await self._client.get(f"{b.url}/health", timeout=httpx.Timeout(3.0, connect=1.0))
                    b.healthy = r.status_code == 200
                except (httpx.HTTPError, httpx.TimeoutException):
                    b.healthy = False
