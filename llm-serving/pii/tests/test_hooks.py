"""검사 훅 순수 함수(병합/마스킹/정책) 단위 테스트. NER 호출 없이 검증."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hooks import Detection, is_specific_address, mask, merge  # noqa: E402


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
