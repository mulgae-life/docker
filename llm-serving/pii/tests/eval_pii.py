"""PII 엔진 정확성 평가 — 한국어 합성 케이스셋.

타입별 precision/recall + 과탐(정상문 과마스킹)을 측정한다. 보험 실데이터
recall 게이트 이전의 1차 회귀 지표로 사용한다.

전제: NER 서버(8911 vmaca123 / 8901 townboy)가 기동된 상태.
실행:  cd pii && python tests/eval_pii.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402

from detectors.ner_client import NerPool  # noqa: E402
from hooks import analyze  # noqa: E402

NER_BACKENDS = [("127.0.0.1", 8911, "vmaca123"), ("127.0.0.1", 8901, "townboy")]
BLOCK_TYPES = ["rrn", "card"]

# (id, text, expected_types, note)
# expected_types: 이 문장에서 검출되어야 하는 PII 타입 집합. 빈 집합이면 '정상문'(검출 0이 정답).
CASES: list[tuple[str, str, set[str], str]] = [
    # ── 구조화 PII (regex + 체크섬, 결정적) ──
    ("rrn_valid",   "제 주민등록번호는 901010-1234560 입니다.",            {"rrn"},     "유효 체크섬"),
    ("rrn_context", "본인확인 위해 9010101234560 알려드립니다.",            {"rrn"},     "하이픈 없음"),
    ("card_luhn",   "결제 카드 4111-1111-1111-1111 로 해주세요.",          {"card"},    "Luhn 유효 Visa"),
    ("brn_valid",   "사업자등록번호 123-45-67891 로 세금계산서 발행요청.", {"brn"},     "사업자 체크섬"),
    ("phone_mobile","연락처는 010-1234-5678 입니다.",                       {"phone"},   "휴대폰"),
    ("phone_seoul", "사무실 02-123-4567 로 전화주세요.",                    {"phone"},   "지역번호"),
    ("account",     "환급 계좌는 110-234-567890 신한은행입니다.",           {"account"}, "계좌"),
    ("email",       "메일은 hong.gildong@example.com 으로 보내주세요.",     {"email"},   "이메일"),

    # ── 비정형 PII (NER) ──
    ("name_only",   "안녕하세요, 홍길동입니다.",                            {"person"},  "이름 단독"),
    ("name_polite", "담당자 김영희 대리에게 전달했습니다.",                 {"person"},  "이름+직책"),
    ("addr_full",   "자택 주소는 서울특별시 강남구 테헤란로 152 입니다.",   {"address"}, "구체 주소"),
    ("addr_road",   "배송지 경기도 성남시 분당구 판교로 235번지로 변경.",   {"address"}, "도로명+번지"),
    ("org_insurer", "한화손해보험에 보험금을 청구했습니다.",                {"org"},     "조직명"),

    # ── 복합 (여러 PII 동시) ──
    ("mix_name_phone", "고객 박철수님 010-9876-5432 으로 안내 부탁드립니다.", {"person", "phone"}, "이름+전화"),
    ("mix_rrn_name",   "이순신 901010-1234560 본인 맞습니다.",               {"person", "rrn"},   "이름+주민"),

    # ── 정상문 (과탐/과마스킹 체크 — 검출 0이 정답) ──
    ("norm_seoul",   "저희 본사는 서울에 있습니다.",                set(), "지명만"),
    ("norm_country", "대한민국 자동차보험 시장은 성숙기입니다.",   set(), "국가명"),
    ("norm_product", "대인배상 한도와 자기부담금이 궁금합니다.",   set(), "보험 용어"),
    ("norm_greet",   "감사합니다. 좋은 하루 되세요.",              set(), "인사말"),
    ("norm_generic", "보험료 납입일을 매월 25일로 변경하고 싶어요.", set(), "숫자(날짜)"),
    ("norm_amount",  "보험금 3000만원을 청구하려 합니다.",         set(), "금액"),
    ("norm_dept_word", "작성부서 칸을 비워두셨습니다.",            set(), "일반어 org 과탐 방지"),
    ("norm_doc_date",  "본 보고서는 2026.05.31 작성되었습니다.",    set(), "작성일→생년월일 오탐 방지"),

    # ── 생년월일 (출생 문맥 있을 때만 PII) ──
    ("birth_context",  "환자 생년월일은 1985.03.12 입니다.",        {"birth"}, "출생 문맥 → 마스킹 유지"),

    # ── 경계 (우회 시도) ──
    ("evade_fullwidth", "주민번호 ９０１０１０－１２３４５６０ 입니다.", {"rrn"}, "전각 우회(NFKC)"),
]


def _fmt_pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):5.1f}%" if d else "  n/a"


async def main() -> int:
    client = httpx.AsyncClient(timeout=10.0)
    pool = NerPool(client, score_threshold=0.5)
    for host, port, tag in NER_BACKENDS:
        pool.add_backend(host, port, tag)
    await pool.health_check()

    healthy = [b for b in pool.backends if getattr(b, "healthy", True)] if hasattr(pool, "backends") else None
    print(f"NER 백엔드: {NER_BACKENDS}  (health_check 완료)\n")

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    overmask_cases: list[str] = []
    miss_rows: list[tuple[str, set[str], set[str], str]] = []

    print(f"{'ID':<16} {'결과':<6} {'기대':<22} {'검출':<22} 마스킹")
    print("─" * 110)
    for cid, text, expected, note in CASES:
        res = await analyze(text, pool, block_types=BLOCK_TYPES,
                            connect_to=1.0, read_to=3.0)
        detected = {d.type for d in res.detections}
        for t in expected & detected:
            tp[t] += 1
        for t in detected - expected:
            fp[t] += 1
        for t in expected - detected:
            fn[t] += 1
        if not expected and detected:
            overmask_cases.append(cid)
        ok = detected == expected
        if not ok:
            miss_rows.append((cid, expected, detected, note))
        mark = "✅OK" if ok else "❌MISS"
        masked_short = res.masked if len(res.masked) <= 40 else res.masked[:37] + "..."
        print(f"{cid:<16} {mark:<6} {str(sorted(expected)):<22} {str(sorted(detected)):<22} {masked_short}")

    print("\n" + "═" * 60)
    print("타입별 precision / recall")
    print("═" * 60)
    all_types = sorted(set(tp) | set(fp) | set(fn))
    print(f"{'type':<10} {'TP':>4} {'FP':>4} {'FN':>4}  {'precision':>10} {'recall':>10}")
    print("─" * 60)
    for t in all_types:
        p = _fmt_pct(tp[t], tp[t] + fp[t])
        r = _fmt_pct(tp[t], tp[t] + fn[t])
        print(f"{t:<10} {tp[t]:>4} {fp[t]:>4} {fn[t]:>4}  {p:>10} {r:>10}")

    total_tp = sum(tp.values())
    total_fp = sum(fp.values())
    total_fn = sum(fn.values())
    print("─" * 60)
    print(f"{'TOTAL':<10} {total_tp:>4} {total_fp:>4} {total_fn:>4}  "
          f"{_fmt_pct(total_tp, total_tp + total_fp):>10} {_fmt_pct(total_tp, total_tp + total_fn):>10}")

    normal_total = sum(1 for _, _, e, _ in CASES if not e)
    print("\n" + "═" * 60)
    print(f"과탐(과마스킹): 정상문 {normal_total}건 중 {len(overmask_cases)}건 오검출"
          f"  → {overmask_cases if overmask_cases else '없음 ✅'}")
    print(f"미스 케이스: {len(miss_rows)}건")
    for cid, exp, det, note in miss_rows:
        print(f"  - {cid:<16} 기대={sorted(exp)} 검출={sorted(det)}  ({note})")

    await client.aclose()
    # 과탐 0 + 전체 recall 충분이면 0, 아니면 1 (CI 게이트용)
    return 0 if not overmask_cases and total_fn == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
