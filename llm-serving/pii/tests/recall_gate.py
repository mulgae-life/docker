"""실데이터 recall 게이트 — 라벨링된 JSONL 데이터셋에 대해 타입별 recall/precision을
측정하고, 핵심 타입이 임계값 미달이면 실패(exit 1)한다.

합성 케이스셋(eval_pii.py)은 분포가 인위적이라 '회귀 지표'일 뿐이다. 보험 등 실데이터의
이름·주소·조직 recall을 보장하려면 라벨링 데이터에 대한 게이트가 필요하다. 실데이터를
지금 볼 수 없어도 이 하버스를 미리 두면, 비식별 라벨링 데이터가 준비되는 즉시 그대로 꽂아
CI 게이트로 돌릴 수 있다.

데이터셋 형식(JSONL, 1줄 = 1샘플; span은 정규화 후 offset 기준):
  {"text": "고객 홍길동 010-1234-5678", "spans": [
     {"type": "person", "start": 3, "end": 6},
     {"type": "phone",  "start": 7, "end": 20}]}

매칭: 예측 span과 정답 span이 (1) 타입 동일 (2) 구간 겹침이면 TP로 본다(NER 표준).
  recall(type)    = 매칭된 정답 수 / 전체 정답 수
  precision(type) = 매칭된 예측 수 / 전체 예측 수

실행:
  PII_RECALL_DATASET=data/insurance_labeled.jsonl python tests/recall_gate.py
  python tests/recall_gate.py data/insurance_labeled.jsonl \
      --min-recall person=0.95,address=0.95,org=0.95
전제: NER 서버(8911 vmaca123 / 8901 townboy) 기동. 구조화 타입은 NER 없이도 동작.
종료코드: 게이트 통과 0 / 미달 1 / 데이터셋 없음 0(스킵, 안내 출력).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402

from detectors.ner_client import NerPool  # noqa: E402
from detectors.normalize import normalize_text  # noqa: E402
from hooks import analyze  # noqa: E402

NER_BACKENDS = [("127.0.0.1", 8911, "vmaca123"), ("127.0.0.1", 8901, "townboy")]
BLOCK_TYPES = ["rrn", "card"]

# 기본 게이트: 보험 실데이터 비정형 PII 핵심 3종(이름/주소/조직) recall 하한.
# 구조화 타입(주민/카드 등)은 결정적이라 별도 게이트 불필요(eval 단위 테스트로 충분).
DEFAULT_MIN_RECALL = {"person": 0.95, "address": 0.95, "org": 0.95}


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def _parse_thresholds(spec: str | None) -> dict[str, float]:
    if not spec:
        return dict(DEFAULT_MIN_RECALL)
    out: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = float(v)
    return out


def _load_dataset(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"[ERROR] {path}:{ln} JSON 파싱 실패: {exc}")
            if "text" not in rec or "spans" not in rec:
                raise SystemExit(f"[ERROR] {path}:{ln} 'text'/'spans' 키 필요")
            records.append(rec)
    return records


async def main() -> int:
    ap = argparse.ArgumentParser(description="실데이터 PII recall 게이트")
    ap.add_argument("dataset", nargs="?", default=os.environ.get("PII_RECALL_DATASET"),
                    help="라벨링 JSONL 경로 (또는 env PII_RECALL_DATASET)")
    ap.add_argument("--min-recall", default=None,
                    help="타입별 하한, 예: person=0.95,address=0.95,org=0.95")
    args = ap.parse_args()

    if not args.dataset:
        print("ℹ️  라벨링 데이터셋이 지정되지 않았습니다(스킵).")
        print("    실데이터(비식별)가 준비되면 아래처럼 실행하세요:")
        print("    PII_RECALL_DATASET=data/insurance_labeled.jsonl python tests/recall_gate.py")
        print("    형식: 줄당 {\"text\": ..., \"spans\": [{\"type\",\"start\",\"end\"}]}")
        return 0
    if not os.path.exists(args.dataset):
        print(f"[ERROR] 데이터셋 없음: {args.dataset}")
        return 1

    thresholds = _parse_thresholds(args.min_recall)
    records = _load_dataset(args.dataset)
    print(f"데이터셋: {args.dataset}  ({len(records)} 샘플)")
    print(f"게이트 하한: {thresholds}\n")

    client = httpx.AsyncClient(timeout=10.0)
    pool = NerPool(client, score_threshold=0.5)
    for host, port, tag in NER_BACKENDS:
        pool.add_backend(host, port, tag)
    await pool.health_check()

    gold_total: dict[str, int] = defaultdict(int)
    gold_hit: dict[str, int] = defaultdict(int)
    pred_total: dict[str, int] = defaultdict(int)
    pred_hit: dict[str, int] = defaultdict(int)

    for rec in records:
        norm = normalize_text(rec["text"])
        gold = [(s["type"], int(s["start"]), int(s["end"])) for s in rec["spans"]]
        res = await analyze(norm, pool, block_types=BLOCK_TYPES,
                            connect_to=1.0, read_to=3.0)
        pred = [(d.type, d.start, d.end) for d in res.detections]

        for gt, gs, ge in gold:
            gold_total[gt] += 1
            if any(pt == gt and _overlap(gs, ge, ps, pe) for pt, ps, pe in pred):
                gold_hit[gt] += 1
        for pt, ps, pe in pred:
            pred_total[pt] += 1
            if any(pt == gt and _overlap(gs, ge, ps, pe) for gt, gs, ge in gold):
                pred_hit[pt] += 1

    await client.aclose()

    types = sorted(set(gold_total) | set(pred_total))
    print(f"{'type':<10} {'recall':>9} {'precision':>10} {'gold':>5} {'pred':>5}  gate")
    print("─" * 55)
    failed: list[str] = []
    for t in types:
        rec_v = gold_hit[t] / gold_total[t] if gold_total[t] else None
        prec_v = pred_hit[t] / pred_total[t] if pred_total[t] else None
        rec_s = f"{rec_v:.3f}" if rec_v is not None else "  n/a"
        prec_s = f"{prec_v:.3f}" if prec_v is not None else "  n/a"
        gate = ""
        if t in thresholds and gold_total[t] > 0:
            if rec_v is None or rec_v < thresholds[t]:
                gate = f"❌ < {thresholds[t]}"
                failed.append(t)
            else:
                gate = f"✅ ≥ {thresholds[t]}"
        print(f"{t:<10} {rec_s:>9} {prec_s:>10} {gold_total[t]:>5} {pred_total[t]:>5}  {gate}")

    print("\n" + ("✅ 게이트 통과" if not failed else f"❌ 게이트 실패: {failed}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
