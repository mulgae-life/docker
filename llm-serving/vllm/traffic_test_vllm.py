#!/usr/bin/env python3
"""vLLM 게이트웨이 보수적 트래픽 테스트.

기능 검증용 test_vllm_server.py와 달리 운영 지표를 확인한다.
기본값은 실제 운영 서버 보호를 우선하여 낮은 강도로 설정한다.

테스트 단계:
  1. 모델 확인: --model 미지정 시 /v1/models에서 첫 모델명을 자동 추출.
  2. 사전 스냅샷: /health, /server-status, /v1/models 상태 저장.
  3. 모드 선택: smoke는 저강도 확인, overload는 429 과부하 차단 확인.
  4. 요청 방식: 기본은 non-stream, --stream 지정 시 SSE와 TTFT 측정.
  5. 트래픽 실행: 요청 큐를 만들고 --concurrency 워커가 순차 처리.
  6. 보호 장치: 허용되지 않은 에러율 또는 연속 실패 기준 초과 시 조기 중단.
  7. 사후 점검: 테스트 후 /health와 /server-status 생존 여부를 통과 조건에 반영.
  8. 지표 집계: 성공률, 429 방어 응답, 상태코드, RPS, TPS, latency, TTFT 집계.
  9. 결과 저장: logs/traffic_*.json 리포트 저장, 통과 기준 미달 시 exit 1.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


PROMPTS = [
    "자동차보험 대인배상 담보를 한 문단으로 설명해주세요.",
    "실손보험 청구 절차를 단계별로 짧게 정리해주세요.",
    "화재보험에서 자주 묻는 보장 제외 항목을 알려주세요.",
    "운전자보험과 자동차보험의 차이를 간단히 비교해주세요.",
]


@dataclass
class RequestResult:
    index: int
    ok: bool
    status: int | str
    latency_ms: float
    ttft_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    error: str


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 30,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return e.code, parsed


def _snapshot(base_url: str, timeout: float) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for path in ("/health", "/server-status", "/v1/models"):
        try:
            status, body = _request_json(f"{base_url}{path}", timeout=timeout)
            data[path] = {"status": status, "body": body}
        except Exception as e:
            data[path] = {"status": "error", "body": f"{type(e).__name__}: {e}"}
    return data


def _resolve_model(base_url: str, model: str | None, timeout: float) -> str:
    if model:
        return model
    status, body = _request_json(f"{base_url}/v1/models", timeout=timeout)
    if status != 200:
        raise RuntimeError(f"/v1/models HTTP {status}: {body}")
    models = body.get("data") or []
    if not models or not models[0].get("id"):
        raise RuntimeError(f"/v1/models 응답에서 모델명을 찾지 못함: {body}")
    return models[0]["id"]


def _chat_once(
    *,
    base_url: str,
    model: str,
    index: int,
    stream: bool,
    max_tokens: int,
    timeout: float,
) -> RequestResult:
    prompt = PROMPTS[index % len(PROMPTS)]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

    start = time.monotonic()
    if stream:
        return _stream_once(base_url, body, index, timeout, start)
    return _non_stream_once(base_url, body, index, timeout, start)


def _is_overload_rejection(result: RequestResult) -> bool:
    return result.status == 429


def _is_allowed_result(args: argparse.Namespace, result: RequestResult) -> bool:
    return result.ok or (args.mode == "overload" and _is_overload_rejection(result))


def _non_stream_once(
    base_url: str,
    body: dict[str, Any],
    index: int,
    timeout: float,
    start: float,
) -> RequestResult:
    try:
        status, payload = _request_json(
            f"{base_url}/v1/chat/completions",
            method="POST",
            body=body,
            timeout=timeout,
        )
        latency_ms = (time.monotonic() - start) * 1000
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        ok = status == 200
        error = "" if ok else _short_error(payload)
        return RequestResult(
            index=index,
            ok=ok,
            status=status,
            latency_ms=latency_ms,
            ttft_ms=None,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            error=error,
        )
    except Exception as e:
        return _exception_result(index, start, e)


def _stream_once(
    base_url: str,
    body: dict[str, Any],
    index: int,
    timeout: float,
    start: float,
) -> RequestResult:
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data,
        headers=headers,
        method="POST",
    )
    usage: dict[str, Any] = {}
    first_token_at: float | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
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
                    if delta.get("content"):
                        first_token_at = time.monotonic()
            latency_ms = (time.monotonic() - start) * 1000
            ttft_ms = None if first_token_at is None else (first_token_at - start) * 1000
            return RequestResult(
                index=index,
                ok=resp.status == 200,
                status=resp.status,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                error="",
            )
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return RequestResult(
            index=index,
            ok=False,
            status=e.code,
            latency_ms=(time.monotonic() - start) * 1000,
            ttft_ms=None,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            error=_short_error(raw),
        )
    except Exception as e:
        return _exception_result(index, start, e)


def _exception_result(index: int, start: float, exc: Exception) -> RequestResult:
    return RequestResult(
        index=index,
        ok=False,
        status=f"{type(exc).__name__}",
        latency_ms=(time.monotonic() - start) * 1000,
        ttft_ms=None,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        error=str(exc),
    )


def _short_error(payload: Any, max_len: int = 500) -> str:
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = str(payload)
    return text[:max_len]


def _run_traffic(args: argparse.Namespace, model: str) -> tuple[list[RequestResult], bool]:
    work: queue.Queue[int] = queue.Queue()
    for i in range(args.requests):
        work.put(i)

    results: list[RequestResult] = []
    lock = threading.Lock()
    stop_event = threading.Event()
    tripped = False

    def should_trip() -> bool:
        if len(results) < args.min_requests_before_break:
            return False
        failures = [r for r in results if not _is_allowed_result(args, r)]
        if len(failures) >= args.max_consecutive_errors:
            tail = results[-args.max_consecutive_errors :]
            if len(tail) == args.max_consecutive_errors and all(
                not _is_allowed_result(args, r) for r in tail
            ):
                return True
        return len(failures) / len(results) > args.max_error_rate

    def worker() -> None:
        nonlocal tripped
        while not stop_event.is_set():
            try:
                idx = work.get_nowait()
            except queue.Empty:
                return
            result = _chat_once(
                base_url=args.base_url,
                model=model,
                index=idx,
                stream=args.stream,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
            with lock:
                results.append(result)
                if should_trip():
                    tripped = True
                    stop_event.set()
            work.task_done()
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    threads = []
    delay = args.ramp_up_seconds / max(args.concurrency - 1, 1)
    for _ in range(args.concurrency):
        t = threading.Thread(target=worker, daemon=True)
        threads.append(t)
        t.start()
        if delay > 0:
            time.sleep(delay)
    for t in threads:
        t.join()
    return sorted(results, key=lambda r: r.index), tripped


def _summarize(args: argparse.Namespace, results: list[RequestResult], elapsed_s: float) -> dict[str, Any]:
    ok_results = [r for r in results if r.ok]
    rejected_results = [r for r in results if _is_overload_rejection(r)]
    failed_results = [r for r in results if not _is_allowed_result(args, r)]
    latencies = [r.latency_ms for r in ok_results]
    ttfts = [r.ttft_ms for r in ok_results if r.ttft_ms is not None]
    completion_tokens = [r.completion_tokens or 0 for r in ok_results]
    total_completion_tokens = sum(completion_tokens)
    status_counts: dict[str, int] = {}
    for r in results:
        key = str(r.status)
        status_counts[key] = status_counts.get(key, 0) + 1

    return {
        "sent": len(results),
        "ok": len(ok_results),
        "overload_rejected": len(rejected_results),
        "allowed": len(results) - len(failed_results),
        "failed": len(failed_results),
        "error_rate": 0 if not results else len(failed_results) / len(results),
        "status_counts": status_counts,
        "elapsed_seconds": elapsed_s,
        "requests_per_second": 0 if elapsed_s <= 0 else len(results) / elapsed_s,
        "completion_tokens_per_second": 0 if elapsed_s <= 0 else total_completion_tokens / elapsed_s,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "p50": statistics.median(latencies) if latencies else None,
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
        "ttft_ms": {
            "p50": statistics.median(ttfts) if ttfts else None,
            "p95": _percentile(ttfts, 0.95),
            "max": max(ttfts) if ttfts else None,
        },
    }


def _postcheck(snapshot: dict[str, Any]) -> dict[str, Any]:
    health_status = snapshot.get("/health", {}).get("status")
    server_status = snapshot.get("/server-status", {}).get("status")
    failures = []
    if health_status != 200:
        failures.append(f"/health HTTP {health_status}")
    if server_status != 200:
        failures.append(f"/server-status HTTP {server_status}")
    return {
        "ok": not failures,
        "health_status": health_status,
        "server_status": server_status,
        "failures": failures,
    }


def _evaluate_pass(
    args: argparse.Namespace,
    summary: dict[str, Any],
    tripped: bool,
    postcheck: dict[str, Any],
) -> dict[str, Any]:
    failures = []
    if tripped:
        failures.append("회로차단 발생")
    if summary["failed"] > 0:
        failures.append(f"허용되지 않은 실패 {summary['failed']}건")
    if args.require_200 and summary["ok"] == 0:
        failures.append("HTTP 200 성공 응답 없음")
    if args.require_429 and summary["overload_rejected"] == 0:
        failures.append("HTTP 429 과부하 차단 응답 없음")
    if not postcheck["ok"]:
        failures.extend(postcheck["failures"])
    return {
        "passed": not failures,
        "mode": args.mode,
        "require_200": args.require_200,
        "require_429": args.require_429,
        "failures": failures,
    }


def _write_report(args: argparse.Namespace, report: dict[str, Any]) -> str:
    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(args.output_dir, f"traffic_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="vLLM 게이트웨이 보수적 트래픽 테스트")
    p.add_argument("--base-url", default="http://localhost:5015", help="게이트웨이 URL")
    p.add_argument("--model", default=None, help="모델명. 미지정 시 /v1/models에서 자동 추출")
    p.add_argument("--mode", choices=("smoke", "overload"), default="smoke", help="테스트 모드")
    p.add_argument("--requests", type=int, default=None, help="총 요청 수")
    p.add_argument("--concurrency", type=int, default=None, help="동시 요청 수")
    p.add_argument("--max-tokens", type=int, default=None, help="요청당 최대 생성 토큰")
    p.add_argument("--timeout", type=float, default=300, help="요청 타임아웃 초")
    p.add_argument("--stream", action="store_true", help="SSE 스트리밍으로 테스트")
    p.add_argument("--ramp-up-seconds", type=float, default=None, help="워커 시작 분산 시간")
    p.add_argument("--sleep-seconds", type=float, default=None, help="워커별 요청 사이 휴식")
    p.add_argument("--max-error-rate", type=float, default=None, help="초과 시 조기 중단")
    p.add_argument("--max-consecutive-errors", type=int, default=None, help="연속 실패 조기 중단 기준")
    p.add_argument("--min-requests-before-break", type=int, default=None, help="회로차단 판단 최소 요청 수")
    p.add_argument(
        "--require-200",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="HTTP 200 응답을 통과 조건으로 요구",
    )
    p.add_argument(
        "--require-429",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="HTTP 429 과부하 차단 응답을 통과 조건으로 요구",
    )
    p.add_argument("--output-dir", default="logs", help="결과 JSON 저장 디렉토리")
    args = p.parse_args()
    args.base_url = args.base_url.rstrip("/")
    defaults = {
        "smoke": {
            "requests": 10,
            "concurrency": 1,
            "max_tokens": 64,
            "ramp_up_seconds": 2.0,
            "sleep_seconds": 0.0,
            "max_error_rate": 0.05,
            "max_consecutive_errors": 2,
            "min_requests_before_break": 3,
            "require_200": True,
            "require_429": False,
        },
        "overload": {
            "requests": 16,
            "concurrency": 10,
            "max_tokens": 256,
            "ramp_up_seconds": 0.0,
            "sleep_seconds": 0.0,
            "max_error_rate": 0.05,
            "max_consecutive_errors": 2,
            "min_requests_before_break": 4,
            "require_200": True,
            "require_429": True,
        },
    }[args.mode]
    for key, value in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    args.effective_defaults = {key: getattr(args, key) for key in defaults}
    if args.requests < 1:
        raise SystemExit("--requests는 1 이상이어야 합니다")
    if args.concurrency < 1:
        raise SystemExit("--concurrency는 1 이상이어야 합니다")
    if args.concurrency > args.requests:
        args.concurrency = args.requests
    return args


def main() -> None:
    args = parse_args()
    model = _resolve_model(args.base_url, args.model, args.timeout)

    print("vLLM 트래픽 테스트")
    print(f"  서버: {args.base_url}")
    print(f"  모델: {model}")
    print(f"  모드: {args.mode}")
    print(f"  요청: {args.requests}, 동시성: {args.concurrency}, stream={args.stream}")

    before = _snapshot(args.base_url, args.timeout)
    start = time.monotonic()
    results, tripped = _run_traffic(args, model)
    elapsed_s = time.monotonic() - start
    after = _snapshot(args.base_url, args.timeout)
    summary = _summarize(args, results, elapsed_s)
    postcheck = _postcheck(after)
    pass_criteria = _evaluate_pass(args, summary, tripped, postcheck)

    report = {
        "config": {
            "base_url": args.base_url,
            "model": model,
            "mode": args.mode,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "max_tokens": args.max_tokens,
            "timeout": args.timeout,
            "stream": args.stream,
            "max_error_rate": args.max_error_rate,
            "max_consecutive_errors": args.max_consecutive_errors,
            "require_200": args.require_200,
            "require_429": args.require_429,
        },
        "effective_defaults": args.effective_defaults,
        "circuit_breaker_tripped": tripped,
        "pass_criteria": pass_criteria,
        "postcheck": postcheck,
        "summary": summary,
        "snapshots": {"before": before, "after": after},
        "results": [asdict(r) for r in results],
    }
    report_path = _write_report(args, report)

    print("")
    print("결과")
    print(f"  성공/전체: {summary['ok']}/{summary['sent']}")
    print(f"  429 방어 응답: {summary['overload_rejected']}")
    print(f"  허용/전체: {summary['allowed']}/{summary['sent']}")
    print(f"  에러율: {summary['error_rate']:.2%}")
    print(f"  상태코드: {summary['status_counts']}")
    print(f"  RPS: {summary['requests_per_second']:.2f}")
    print(f"  completion TPS: {summary['completion_tokens_per_second']:.2f}")
    print(
        "  latency p50/p95/p99(ms): "
        f"{summary['latency_ms']['p50']} / "
        f"{summary['latency_ms']['p95']} / "
        f"{summary['latency_ms']['p99']}"
    )
    if args.stream:
        print(f"  TTFT p50/p95(ms): {summary['ttft_ms']['p50']} / {summary['ttft_ms']['p95']}")
    print(f"  회로차단: {tripped}")
    print(
        "  사후 생존 확인: "
        f"{postcheck['ok']} "
        f"(/health={postcheck['health_status']}, /server-status={postcheck['server_status']})"
    )
    print(f"  통과: {pass_criteria['passed']}")
    if pass_criteria["failures"]:
        print(f"  실패 사유: {pass_criteria['failures']}")
    print(f"  리포트: {report_path}")

    if not pass_criteria["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
