"""검사 훅 순수 함수(병합/마스킹/정책) 단위 테스트. NER 호출 없이 검증."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hooks import (  # noqa: E402
    AnalysisResult,
    Detection,
    _filter_generic_org,
    _filter_nonbirth_dates,
    is_specific_address,
    mask,
    merge,
)


def test_merge_overlapping_address():
    """vmaca 통짜주소 + townboy 조각이 하나의 span으로 병합돼야 한다."""
    dets = [
        Detection("address", 0, 18, "ner:vmaca123"),
        Detection("address", 0, 5, "ner:townboy"),
        Detection("address", 6, 9, "ner:townboy"),
    ]
    m = merge(dets)
    assert len(m) == 1
    assert (m[0].start, m[0].end) == (0, 18)


def test_merge_disjoint_kept():
    dets = [Detection("person", 0, 3, "ner"), Detection("phone", 10, 23, "regex")]
    assert len(merge(dets)) == 2


def test_merge_priority_type():
    """겹치는 person(NER)과 rrn(regex)은 우선순위 높은 rrn으로 대표 타입 결정."""
    dets = [Detection("person", 0, 10, "ner"), Detection("rrn", 2, 15, "regex")]
    m = merge(dets)
    assert len(m) == 1
    assert m[0].type == "rrn"
    assert (m[0].start, m[0].end) == (0, 15)


def test_mask_label_replacement():
    text = "ABC홍길동DEF"
    masked = mask(text, [Detection("person", 3, 6, "ner")])
    assert masked == "ABC[이름]DEF"


def test_mask_multiple_offset_preserved():
    # 두 구간 마스킹 시 뒤에서부터 치환해 offset 깨지지 않아야 한다.
    text = "0123456789"
    dets = [Detection("person", 0, 2, "x"), Detection("phone", 5, 8, "y")]
    assert mask(text, dets) == "[이름]234[전화번호]89"


def test_empty():
    assert merge([]) == []
    assert mask("hello", []) == "hello"


def test_address_specificity():
    """단순 지명/국가는 비구체(False), 도로명·번지·동 포함은 구체(True)."""
    assert is_specific_address("서울") is False
    assert is_specific_address("대한민국") is False
    assert is_specific_address("강남구") is False
    assert is_specific_address("서울특별시 강남구 테헤란로 152") is True
    assert is_specific_address("역삼동") is True
    assert is_specific_address("판교아파트 101호") is True


def test_filter_generic_org_removes_standalone():
    """단독 일반어/접미어 org(작성부서·센터)는 제거, 실제 조직명은 보존."""
    text = "작성부서 디지털AI센터 센터"
    def span(word: str, src: str, occ: int = 0) -> Detection:
        i = -1
        for _ in range(occ + 1):
            i = text.index(word, i + 1)
        return Detection("org", i, i + len(word), src)
    dets = [
        span("작성부서", "ner:townboy"),      # 일반어 → 제거
        span("디지털AI센터", "ner:vmaca123"),  # 조직명 → 보존
        span("센터", "ner:townboy", occ=1),    # 끝의 단독 "센터" 부분 span → 제거
    ]
    out = _filter_generic_org(text, dets)
    assert len(out) == 1
    assert text[out[0].start:out[0].end] == "디지털AI센터"


def test_filter_nonbirth_dates():
    """출생 문맥 없는 날짜는 제거, 문맥 있는 생년월일은 보존."""
    # 문맥 없음(작성일) → 제거
    t1 = "2026.05.31 보고서"
    assert _filter_nonbirth_dates(t1, [Detection("birth", 0, 10, "ner")]) == []
    # 문맥 있음(생년월일 레이블) → 보존
    t2 = "생년월일 1990.05.31"
    out = _filter_nonbirth_dates(t2, [Detection("birth", 5, 15, "ner")])
    assert len(out) == 1


def test_skip_mask_types_keeps_detection_but_unmasks():
    """skip_mask_types(org)는 detection은 남기되 마스킹에서만 제외(감사 추적 유지)."""
    text = "담당자 홍길동 소속 디지털AI센터"
    dets = [Detection("person", 4, 7, "ner"), Detection("org", 11, 17, "ner")]
    res = AnalysisResult(text=text, detections=dets, has_block=False,
                         skip_mask_types=frozenset({"org"}))
    # 이름은 마스킹, 조직은 노출
    assert res.masked == "담당자 [이름] 소속 디지털AI센터"
    # detection 자체는 보존돼 감사에 남는다
    assert len(res.detections) == 2
    assert res.is_skipped(dets[1]) is True
    assert res.is_skipped(dets[0]) is False
