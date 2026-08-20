"""vLLM 속도 측정 테스트 — 모델 간 처리량/지연 비교용.

매트릭스: 동시성 × 출력 토큰 (입력은 ~2000자 한국어 RAG 컨텍스트 고정)
지표: TTFT(p50), decode TPS(p50) — 텍스트 출력 속도
출력: results/speed_results.md 에 누적 append (Markdown 테이블)

모델명은 `{base_url}/v1/models` API에서 자동 추출. `--model`로 명시도 가능.
여러 게이트웨이(모델) 비교는 같은 결과 파일에 두 번 누적하면 됨.

기본 실행:
    python tests/speed_test.py                                       # localhost:5015
    python tests/speed_test.py --base-url http://localhost:5015      # Gemma 게이트웨이
    python tests/speed_test.py --base-url http://localhost:5016      # Qwen 게이트웨이 (같은 results에 누적)
    python tests/speed_test.py --base-url http://localhost:5015 --quick   # 빠른 검증
    python tests/speed_test.py --base-url http://localhost:5015 --model my-model --label "MyLabel"

매트릭스 기본값:
    동시성:   [1, 5, 10]
    입력:     ~2000자 한국어 RAG 컨텍스트 고정 (PROMPT_KO_CONTEXT)
    max_tok:  [512, 2048]
    조합 6개 / 게이트웨이당
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


# ── 모델 메타 ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelEndpoint:
    model: str          # served_model_name (`/v1/models`에서 자동 추출 or --model로 명시)
    base_url: str       # 게이트웨이 URL (`--base-url`)
    label: str          # 결과 테이블 표시명 (기본은 model 그대로)


def _resolve_model_name(base_url: str, override: Optional[str], timeout: float = 5.0) -> str:
    """`--model` 명시가 있으면 그대로 반환, 없으면 `{base_url}/v1/models` 첫 결과 사용."""
    if override:
        return override
    url = f"{base_url.rstrip('/')}/v1/models"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(
            f"{url} 호출 실패 — 게이트웨이가 살아있는지, --model을 직접 지정하세요. ({type(e).__name__}: {e})"
        )
    items = payload.get("data") or []
    if not items:
        raise RuntimeError(f"{url} 응답이 비어있음 — --model을 직접 지정하세요.")
    return items[0]["id"]


def _endpoint_label(base_url: str) -> str:
    """결과 테이블 `endpoint` 컬럼용 짧은 표시. http://host:port → host:port."""
    parsed = urlparse(base_url)
    return parsed.netloc or parsed.path or base_url


# ── 입력 프롬프트 (~2000자 한국어 RAG 컨텍스트 고정) ────────────────
# 토크나이저별 다르지만 한국어 1자 ≈ 1.5~2 토큰, 본 컨텍스트 ≈ 2500~3500 입력 토큰.
# prefill 부담을 어느 정도 주면서도 모든 모델에서 max_model_len(32768) 안에 충분히 들어가는 길이.
PROMPT_KO_CONTEXT = """다음은 한국의 기후, 농업, 발효 식품에 관한 자료입니다. 이 자료를 참고해 마지막 질문에 답변해주세요.

[자료 1: 한국의 기후 특성]
한국은 사계절이 뚜렷한 온대 몬순 기후 지역에 속한다. 여름철에는 고온다습한 북태평양 기단의 영향을 받아 평균 기온이 25도 이상으로 올라가고 강수량이 집중되며, 겨울철에는 한랭건조한 시베리아 기단의 영향으로 영하 10도 이하로 떨어지는 일이 잦다. 봄과 가을은 이동성 고기압이 지배하여 비교적 건조하고 화창한 날씨가 이어진다. 연 강수량은 1000~1400mm로 동아시아 평균보다 다소 많은 편이며, 6~9월에 연 강수량의 60~70%가 집중되는 하계 강수형이다. 이러한 기후 조건은 벼농사 중심의 농업 발달, 그리고 겨울철 식량 보존을 위한 발효 식품 문화의 토대가 되었다.

[자료 2: 농업과 작물]
한국의 주요 작물은 벼, 보리, 콩, 배추, 무, 고추, 마늘, 생강 등이다. 벼는 여름철 고온다습 기후에 적합하며 전 국토 농경지의 절반 이상에서 재배된다. 보리는 가을에 파종하여 이듬해 초여름에 수확하는 이모작 작물로, 식량 자급 향상에 기여해왔다. 배추와 무는 가을철 김장의 주재료로 9~11월에 집중 재배된다. 고추, 마늘, 생강은 양념 채소이자 발효 식품의 핵심 향신 재료로 연중 사용된다. 콩은 된장, 간장, 청국장의 원료가 되는 단백질 공급원으로 한반도 자생 작물이다.

[자료 3: 김치의 발효 과학]
김치는 배추, 무, 오이 등 채소를 소금에 절인 뒤 고춧가루, 마늘, 생강, 젓갈, 파 등을 양념으로 버무려 발효시키는 한국의 대표적 발효 식품이다. 발효 초기에는 류코노스톡 메센테로이데스 같은 헤테로형 유산균이 우점하여 이산화탄소와 유산을 동시에 생산하며 청량한 맛을 만든다. 발효 중기에는 락토바실러스 플란타룸과 락토바실러스 사케이 등 호모형 유산균이 증식하여 유산 농도를 높이고 산미를 강하게 한다. 발효 후기에는 pH가 4.0 이하로 떨어지면서 잡균 증식이 억제되어 보존성이 높아진다. 적정 발효 온도는 4~10도이며, 이 범위에서 2~3주 발효시켰을 때 가장 균형 잡힌 산미와 감칠맛이 형성된다.

[자료 4: 된장과 간장]
콩으로 만드는 된장과 간장도 한국 발효 식품의 양대 축이다. 삶은 콩을 메주로 빚어 짚으로 묶어 매달아두면 자연 환경의 곰팡이 균인 아스페르길루스 오리제와 바실러스 서브틸리스 등이 부착해 콩 단백질을 펩타이드와 아미노산으로 분해한다. 메주를 소금물에 담가 일정 기간 숙성시키면 액체 부분은 간장이 되고 고체 부분은 된장이 되며, 이 과정에서 글루탐산 같은 감칠맛 성분이 풍부하게 형성된다. 전통 방식의 자연 발효는 6개월에서 1년 이상 걸리지만, 균주 접종과 온도 조절을 통한 산업 생산에서는 3~6개월로 단축할 수 있다.

[질문]
"""


# ── 자료구조 ─────────────────────────────────────────────────────
@dataclass
class Scenario:
    concurrency: int
    max_tokens: int      # 512 | 2048


@dataclass
class RequestSample:
    ok: bool
    status: object       # int(HTTP) | str(예외 클래스명)
    ttft_ms: Optional[float]
    latency_ms: float
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    error: str = ""


@dataclass
class Aggregate:
    ok: int
    rejected_429: int
    errored: int
    ttft_p50_ms: Optional[float]
    ttft_p95_ms: Optional[float]
    decode_tps_p50: Optional[float]
    decode_tps_p95: Optional[float]
    server_tps: Optional[float]
    itl_p50_ms: Optional[float]
    elapsed_s: float
    prompt_tokens_avg: Optional[float]
    completion_tokens_avg: Optional[float]


def _build_prompt() -> str:
    # 입력은 한 가지로 고정. 모델이 max_tokens 한도까지 채우도록 "자세하고 충분한 분량" 지시.
    return f"{PROMPT_KO_CONTEXT}위 자료를 참고해 한국어로 자세하고 충분한 분량으로 답변해주세요."


# ── 스트리밍 호출 (TTFT 측정용) ───────────────────────────────────
def _stream_once(endpoint: ModelEndpoint, prompt: str, max_tokens: int, timeout: float) -> RequestSample:
    url = f"{endpoint.base_url}/v1/chat/completions"
    body = {
        "model": endpoint.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    first_token_at: Optional[float] = None
    usage: dict = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                item = line[5:].strip()
                if item == "[DONE]":
                    break
                try:
                    chunk = json.loads(item)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if choices and first_token_at is None:
                    delta = choices[0].get("delta") or {}
                    content = (
                        delta.get("content")
                        or delta.get("reasoning_content")
                        or delta.get("reasoning")
                    )
                    if content:
                        first_token_at = time.monotonic()
            latency_ms = (time.monotonic() - start) * 1000
            ttft_ms = None if first_token_at is None else (first_token_at - start) * 1000
            return RequestSample(
                ok=resp.status == 200,
                status=resp.status,
                ttft_ms=ttft_ms,
                latency_ms=latency_ms,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return RequestSample(
            ok=False,
            status=e.code,
            ttft_ms=None,
            latency_ms=(time.monotonic() - start) * 1000,
            prompt_tokens=None,
            completion_tokens=None,
            error=body_text or str(e.code),
        )
    except Exception as e:
        return RequestSample(
            ok=False,
            status=type(e).__name__,
            ttft_ms=None,
            latency_ms=(time.monotonic() - start) * 1000,
            prompt_tokens=None,
            completion_tokens=None,
            error=str(e)[:200],
        )


# ── 시나리오 실행 ─────────────────────────────────────────────────
def _run_scenario(
    endpoint: ModelEndpoint,
    scenario: Scenario,
    total_requests: int,
    timeout: float,
) -> tuple[list[RequestSample], float]:
    prompt = _build_prompt()
    samples: list[RequestSample] = []
    start_wall = time.monotonic()
    with ThreadPoolExecutor(max_workers=scenario.concurrency) as ex:
        futures = [
            ex.submit(_stream_once, endpoint, prompt, scenario.max_tokens, timeout)
            for _ in range(total_requests)
        ]
        for f in as_completed(futures):
            samples.append(f.result())
    elapsed_s = time.monotonic() - start_wall
    return samples, elapsed_s


# ── 집계 ─────────────────────────────────────────────────────────
def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    vs = sorted(values)
    k = (len(vs) - 1) * pct
    f = int(k)
    c = min(f + 1, len(vs) - 1)
    if f == c:
        return vs[f]
    return vs[f] + (vs[c] - vs[f]) * (k - f)


def _aggregate(samples: list[RequestSample], elapsed_s: float) -> Aggregate:
    ok = [s for s in samples if s.ok]
    rejected = [s for s in samples if s.status == 429]
    errored = [s for s in samples if not s.ok and s.status != 429]

    ttfts = [s.ttft_ms for s in ok if s.ttft_ms is not None]

    decode_tps: list[float] = []
    for s in ok:
        if s.completion_tokens and s.ttft_ms is not None:
            decode_time_s = (s.latency_ms - s.ttft_ms) / 1000
            if decode_time_s > 0:
                decode_tps.append(s.completion_tokens / decode_time_s)

    total_completion = sum(s.completion_tokens or 0 for s in ok)
    server_tps = total_completion / elapsed_s if elapsed_s > 0 and total_completion > 0 else None

    itl_p50 = None
    decode_p50 = _percentile(decode_tps, 0.5)
    if decode_p50 and decode_p50 > 0:
        itl_p50 = 1000 / decode_p50

    prompt_tokens_avg = (
        statistics.mean(s.prompt_tokens for s in ok if s.prompt_tokens)
        if any(s.prompt_tokens for s in ok)
        else None
    )
    completion_tokens_avg = (
        statistics.mean(s.completion_tokens for s in ok if s.completion_tokens)
        if any(s.completion_tokens for s in ok)
        else None
    )

    return Aggregate(
        ok=len(ok),
        rejected_429=len(rejected),
        errored=len(errored),
        ttft_p50_ms=_percentile(ttfts, 0.5),
        ttft_p95_ms=_percentile(ttfts, 0.95),
        decode_tps_p50=decode_p50,
        decode_tps_p95=_percentile(decode_tps, 0.95),
        server_tps=server_tps,
        itl_p50_ms=itl_p50,
        elapsed_s=elapsed_s,
        prompt_tokens_avg=prompt_tokens_avg,
        completion_tokens_avg=completion_tokens_avg,
    )


# ── Markdown 출력 ────────────────────────────────────────────────
COLUMNS = [
    "timestamp",
    "model",
    "concurrency",
    "max_tok",
    "ok/N",
    "TTFT_ms",
    "TPS",
]

HEADER_LINE = "| " + " | ".join(COLUMNS) + " |"
DIVIDER_LINE = "|" + "|".join("---" for _ in COLUMNS) + "|"


def _fmt(v: Optional[float], fmt: str = ".1f") -> str:
    if v is None:
        return "-"
    return f"{v:{fmt}}"


def _row(
    timestamp: str,
    endpoint: ModelEndpoint,
    scenario: Scenario,
    total_requests: int,
    agg: Aggregate,
) -> str:
    return "| " + " | ".join([
        timestamp,
        endpoint.label,
        str(scenario.concurrency),
        str(scenario.max_tokens),
        f"{agg.ok}/{total_requests}",
        _fmt(agg.ttft_p50_ms),
        _fmt(agg.decode_tps_p50),
    ]) + " |"


def _ensure_md_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# vLLM Speed Test Results\n\n"
        "동시성 × 출력 토큰 매트릭스 기준 속도 측정 누적 결과. 실행할 때마다 행이 추가됩니다.\n\n"
        "**테스트 조건**\n"
        "- 입력 프롬프트: 한국어 RAG 컨텍스트 ~2000자 고정 (`PROMPT_KO_CONTEXT`)\n"
        "- 매트릭스: 동시성 [1, 5, 10] × max_tokens [512, 2048] = 6 시나리오 / 게이트웨이\n\n"
        "**컬럼**\n"
        "- `TTFT_ms`: 첫 토큰까지 지연 (ms, prefill 성능)\n"
        "- `TPS`: 요청당 출력 토큰 생성 속도 (output tok/s, decode 성능 = 텍스트 출력 속도)\n"
        "- `ok/N`: 성공 요청 / 전체 요청 (실패 섞이면 TPS가 왜곡되니 확인용)\n\n"
        f"{HEADER_LINE}\n{DIVIDER_LINE}\n",
        encoding="utf-8",
    )


def _append_row(path: Path, row: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(row + "\n")


# ── 매트릭스 ─────────────────────────────────────────────────────
def _matrix(quick: bool) -> list[Scenario]:
    if quick:
        return [Scenario(concurrency=1, max_tokens=512)]
    return [
        Scenario(c, m)
        for c in (1, 5, 10)
        for m in (512, 2048)
    ]


def _requests_per_scenario(concurrency: int) -> int:
    # 동시성에 비례한 적정 표본 수 (p50/p95 통계 의미가 있도록)
    return max(2 * concurrency, 5)


# ── 메인 ─────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="vLLM 게이트웨이 속도 측정 (매트릭스 기반 누적 append)",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:5015",
        help="게이트웨이 URL (기본 http://localhost:5015). 모델명은 /v1/models에서 자동 추출",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="명시 시 /v1/models 자동 추출을 건너뛰고 이 값을 그대로 사용",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="결과 테이블 model 컬럼 표시명 (기본: 추출/지정된 모델명)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="동시성 1, max_tokens 512 한 건만 측정 (구문/연결 확인용)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="요청당 타임아웃 초 (기본 180)",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        default=Path(__file__).resolve().parent / "results" / "speed_results.md",
        help="결과 Markdown 경로 (기본 tests/results/speed_results.md)",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="워밍업 1회 호출 생략",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    try:
        model_name = _resolve_model_name(base_url, args.model)
    except RuntimeError as e:
        print(f"[speed_test] 모델명 확정 실패: {e}", file=sys.stderr)
        return 2

    endpoint = ModelEndpoint(
        model=model_name,
        base_url=base_url,
        label=args.label or model_name,
    )
    scenarios = _matrix(args.quick)

    _ensure_md_header(args.results_path)

    print(f"[speed_test] base_url={endpoint.base_url}")
    print(f"[speed_test] model={endpoint.model}  label={endpoint.label}")
    # API 노출명이 백엔드와 무관하게 고정돼 있어, 라벨이 없으면 결과 표의 model 열이
    # 전부 같은 이름으로 쌓여 모델 간 비교가 불가능해진다.
    if not args.label:
        print("[speed_test] --label 미지정 — 결과 표에 백엔드 실모델이 남지 않습니다")
    print(f"[speed_test] 시나리오 {len(scenarios)}개 → 결과: {args.results_path}")

    if not args.no_warmup:
        print("  warmup ...", end=" ", flush=True)
        warmup = _stream_once(endpoint, "안녕하세요.", 16, args.timeout)
        print(
            f"ok={warmup.ok} status={warmup.status} "
            f"ttft={_fmt(warmup.ttft_ms)}ms err={warmup.error or '-'}"
        )
        if not warmup.ok:
            print("  ! 워밍업 실패 — 본 측정을 중단합니다.", file=sys.stderr)
            return 3

    for scenario in scenarios:
        n = _requests_per_scenario(scenario.concurrency)
        print(
            f"  · c={scenario.concurrency} max_tok={scenario.max_tokens} N={n}",
            end=" ... ",
            flush=True,
        )
        samples, elapsed = _run_scenario(endpoint, scenario, n, args.timeout)
        agg = _aggregate(samples, elapsed)
        print(
            f"ok={agg.ok}/{n} 429={agg.rejected_429} err={agg.errored} "
            f"TTFT_p50={_fmt(agg.ttft_p50_ms)}ms "
            f"decTPS_p50={_fmt(agg.decode_tps_p50)} "
            f"svrTPS={_fmt(agg.server_tps)}"
        )

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _append_row(args.results_path, _row(timestamp, endpoint, scenario, n, agg))

    print(f"\n[speed_test] 완료. 결과: {args.results_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
