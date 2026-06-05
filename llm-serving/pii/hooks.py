"""PII 검사 훅 — 구조화(regex)+비정형(NER) 통합, 겹침 병합, 차단/마스킹 정책.

흐름(in/out 공통):
  1) normalize(NFKC) → 전각 우회 차단
  2) 구조화 detect(regex+체크섬) + 비정형 NER detect 를 union
  3) 겹침 span 병합(townboy 주소 조각 + vmaca 통짜주소 → 하나로)
  4) 정책: 차단 타입(주민/카드 등) 포함 시 block, 아니면 mask

비동기는 NER 호출뿐이고 병합·마스킹은 순수 함수라 단위 테스트가 쉽다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from detectors.ner_client import NerPool, NerSpan
from detectors.normalize import normalize_text
from detectors.structured import PiiSpan
from detectors.structured import detect as detect_structured

# 통합 PII 타입 → 마스킹 라벨(한국어). 부분 마스킹은 재식별 위험이라 전체 라벨 치환.
_TYPE_KO = {
    "rrn": "주민등록번호", "card": "신용카드번호", "brn": "사업자등록번호",
    "account": "계좌번호",
    "phone": "전화번호", "email": "이메일", "passport": "여권번호",
    "alien": "외국인등록번호", "driver": "운전면허번호", "birth": "생년월일",
    "person": "이름", "address": "주소", "org": "조직",
}

# 병합 시 대표 타입 결정 우선순위(식별력 높은 것 우선). 라벨/감사용.
# ※ structured.detect가 내는 모든 타입(rrn/card/brn/account/phone/email)을 빠짐없이
#    포함해야 한다. 누락 타입은 .get(...,0)으로 0순위가 되어, 같은 구간을 잡은 NER
#    span(예: account)에 병합에서 밀려 라벨이 뒤바뀐다(brn→account 회귀 방지).
_PRIORITY = {
    "rrn": 13, "card": 12, "passport": 11, "alien": 10, "driver": 9,
    "brn": 8, "account": 7, "birth": 6, "phone": 5, "email": 4,
    "person": 3, "org": 2, "address": 1,
}


@dataclass(frozen=True)
class Detection:
    type: str
    start: int
    end: int
    source: str  # "regex" | "ner:<model>"

    @property
    def label_ko(self) -> str:
        return _TYPE_KO.get(self.type, self.type)


@dataclass
class AnalysisResult:
    text: str                       # 정규화된 텍스트(offset 기준)
    detections: list[Detection]
    has_block: bool                 # 차단 대상(block_types) 포함 여부
    skip_mask_types: frozenset[str] = frozenset()  # 서비스 정책상 마스킹만 스킵(감사엔 기록)

    @property
    def masked(self) -> str:
        # skip_mask_types(예: org)는 detection은 남기되 마스킹에서만 제외한다.
        dets = [d for d in self.detections if d.type not in self.skip_mask_types]
        return mask(self.text, dets)

    def is_skipped(self, d: Detection) -> bool:
        """서비스 토글로 마스킹이 스킵된 detection인지(감사 action 구분용)."""
        return d.type in self.skip_mask_types


def _to_detections(s_spans: list[PiiSpan], n_spans: list[NerSpan]) -> list[Detection]:
    dets = [Detection(s.type, s.start, s.end, "regex") for s in s_spans]
    dets += [Detection(n.type, n.start, n.end, f"ner:{n.model}") for n in n_spans]
    return dets


def merge(dets: list[Detection]) -> list[Detection]:
    """겹치거나 인접한 span을 하나로 병합한다(경계 union, 대표 타입=우선순위 max)."""
    if not dets:
        return []
    ordered = sorted(dets, key=lambda d: (d.start, d.end))
    merged: list[Detection] = []
    cur_s, cur_e = ordered[0].start, ordered[0].end
    cur_best = ordered[0]
    for d in ordered[1:]:
        if d.start <= cur_e:  # 겹침/인접 → 병합
            cur_e = max(cur_e, d.end)
            if _PRIORITY.get(d.type, 0) > _PRIORITY.get(cur_best.type, 0):
                cur_best = d
        else:
            merged.append(Detection(cur_best.type, cur_s, cur_e, cur_best.source))
            cur_s, cur_e, cur_best = d.start, d.end, d
    merged.append(Detection(cur_best.type, cur_s, cur_e, cur_best.source))
    return merged


def mask(text: str, dets: list[Detection]) -> str:
    """병합된 detection 구간을 `[라벨]`로 치환(뒤에서부터, offset 보존)."""
    for d in sorted(dets, key=lambda x: x.start, reverse=True):
        text = text[: d.start] + f"[{d.label_ko}]" + text[d.end :]
    return text


# 주소 구체성 — NER이 단순 지명(서울)도 LOCATION으로 잡아 과마스킹하므로,
# 도로명/번지/동/숫자 등 '구체 주소' 신호가 있을 때만 address를 PII로 채택한다.
_ADDR_SPECIFIC_RE = re.compile(r"(로|길|동|읍|면|리|번지|호|층|아파트|빌딩|타워|[0-9])")


def is_specific_address(text: str) -> bool:
    """구체 주소면 True. 단순 지명(서울·대한민국)·행정구역 단독은 False."""
    return bool(_ADDR_SPECIFIC_RE.search(text))


def _filter_vague_address(text: str, dets: list[Detection]) -> list[Detection]:
    """address 타입 중 구체 주소가 아닌 단순 지명은 제거한다."""
    return [d for d in dets
            if not (d.type == "address" and not is_specific_address(text[d.start:d.end]))]


# org 단독 일반어/접미어 — 단독으로는 조직명이 아니다(부분 span·일반어 과탐 방지).
# 예: townboy가 "디지털AI센터"에서 "센터"만 잡거나, "작성부서"라는 일반어를 org로 잡는 경우.
# span 전체가 이 집합과 정확히 일치할 때만 제거하므로 "인사부"·"AI센터" 등 실제 조직명은 보존된다.
_ORG_GENERIC = frozenset({
    "센터", "팀", "그룹", "본부", "실", "과", "부", "부서", "작성부서",
    "회사", "사업부", "지점", "지사", "지부", "협력사", "담당부서",
})


def _filter_generic_org(text: str, dets: list[Detection]) -> list[Detection]:
    """org 중 단독 일반어/접미어(센터·팀·작성부서…)는 조직명이 아니므로 제거한다."""
    return [d for d in dets
            if not (d.type == "org" and text[d.start:d.end].strip() in _ORG_GENERIC)]


# 생년월일 문맥 신호 — 일반 날짜(보고서 작성일 등)를 생년월일로 오탐하는 것을 막는다.
# 주소 구체성 필터와 동일 취지로, span 앞 윈도우에 출생 단서가 있을 때만 birth를 PII로 채택.
_BIRTH_CONTEXT_RE = re.compile(r"(생년월일|생년|생일|출생|년생|D\.?O\.?B)")


def _filter_nonbirth_dates(text: str, dets: list[Detection]) -> list[Detection]:
    """birth 중 출생 문맥 신호가 없는 일반 날짜는 제거한다(작성일·기준일 과마스킹 방지)."""
    out: list[Detection] = []
    for d in dets:
        if d.type == "birth":
            ctx = text[max(0, d.start - 12):d.end]  # span 앞 12자 + span 자체
            if not _BIRTH_CONTEXT_RE.search(ctx):
                continue
        out.append(d)
    return out


async def analyze(
    text: str,
    ner_pool: NerPool | None,
    *,
    block_types: list[str],
    connect_to: float,
    read_to: float,
    skip_mask_types: frozenset[str] = frozenset(),
) -> AnalysisResult:
    """텍스트를 정규화→구조화+NER detect→병합→과탐 필터→정책 판정한다.

    NER 풀 장애 시 `NerPool.detect`가 NerUnavailable을 던지며, 호출 측(프록시)이
    fail-closed로 처리한다. 구조화 regex는 NER과 무관하게 항상 동작(graceful degrade).

    skip_mask_types: 서비스 토글(X-PII-Ignore-Types)로 마스킹만 스킵할 타입.
        detection·감사는 그대로 남기고 `masked`에서만 제외한다.
    """
    norm = normalize_text(text)
    s_spans = detect_structured(norm)
    n_spans: list[NerSpan] = []
    if ner_pool is not None:
        n_spans = await ner_pool.detect(norm, connect_to=connect_to, read_to=read_to)
    dets = merge(_to_detections(s_spans, n_spans))
    dets = _filter_vague_address(norm, dets)   # 단순 지명 과마스킹 방지
    dets = _filter_generic_org(norm, dets)     # 부분 span·일반어 org 과마스킹 방지
    dets = _filter_nonbirth_dates(norm, dets)  # 일반 날짜→생년월일 오탐 방지
    has_block = any(d.type in block_types for d in dets)
    return AnalysisResult(text=norm, detections=dets, has_block=has_block,
                          skip_mask_types=skip_mask_types)
