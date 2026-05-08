#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# logging.sh — vLLM 호출 로그를 5분마다 S3로 동기화 (호출 카운트 통계용)
#
# 사용법:
#   ./logging.sh              시작 (기본 인스턴스 'gemma')
#   ./logging.sh prd-gemma    시작 (인스턴스 'prd-gemma')
#   ./logging.sh stop         중지
#
# 환경변수 (선택, 기본값으로 충분):
#   S3_BUCKET  기본 hgi-ai-res
#   S3_PREFIX  기본 logs/vllm
#   INTERVAL   기본 300              ← 동기화 간격(초)
#
# 산출:
#   S3 키 : s3://$S3_BUCKET/$S3_PREFIX/<INST>/<YYYY-MM-DD>.log
#   호출 수: aws s3 cp s3://hgi-ai-res/logs/vllm/gemma/$(date +%F).log - | wc -l
#
# ⚠ 운영 안전성:
#   vLLM 로그 파일을 read-only(stat / tail -c / grep)로 읽기만 합니다.
#   vLLM 프로세스/포트/파일에 일절 손대지 않으므로 서비스 영향 없음.
#   필터링: 'POST /v1/' 라인만 추출 (그 외 /health, throughput 라인은 무시).
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# 인자 1개가 있으면 인스턴스명으로 사용 ('stop' 제외). 그 외는 기본값 'gemma'.
INST="prd-gemma"
[ "${1:-}" != "stop" ] && [ -n "${1:-}" ] && INST="$1"

S3_BUCKET="${S3_BUCKET:-hgi-ai-res}"
S3_PREFIX="${S3_PREFIX:-logs/vllm}"
INTERVAL="${INTERVAL:-300}"
PID="logs/.logging.pid"

# ── stop ────────────────────────────────────────────────────────
if [ "${1:-}" = "stop" ]; then
    if [ -s "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
        kill "$(cat "$PID")" && echo "stopped (PID $(cat "$PID"))"
    else
        echo "not running"
    fi
    : > "$PID" 2>/dev/null || true
    exit 0
fi

# ── start ───────────────────────────────────────────────────────
if [ -s "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
    echo "already running (PID $(cat "$PID"))"
    exit 0
fi
mkdir -p logs/sync

(
    trap '' HUP   # ssh 끊겨도 살아남기
    while :; do
        SRC="logs/vllm_${INST}.log"
        STATE="logs/.${INST}.offset"
        TODAY="$(date +%F)"
        OUT="logs/sync/${INST}-${TODAY}.log"
        if [ -f "$SRC" ]; then
            prev="$(cat "$STATE" 2>/dev/null || echo 0)"
            size="$(stat -c %s "$SRC")"
            [ "$size" -lt "$prev" ] && prev=0    # vllm 재기동 truncate 감지
            if [ "$size" -gt "$prev" ]; then
                tail -c +$((prev + 1)) "$SRC" | grep -aE 'POST /v1/' >> "$OUT" || true
            fi
            echo "$size" > "$STATE"
            aws s3 cp "$OUT" "s3://${S3_BUCKET}/${S3_PREFIX}/${INST}/${TODAY}.log" --only-show-errors \
                && echo "[$(date '+%F %T')] sync $INST $(wc -l < "$OUT") lines"
        else
            echo "[$(date '+%F %T')] skip — $SRC 없음"
        fi
        sleep "$INTERVAL"
    done
) >> logs/logging.out 2>&1 &

echo $! > "$PID"
disown
echo "started (PID $!) → s3://${S3_BUCKET}/${S3_PREFIX}/${INST}/, interval=${INTERVAL}s"
echo "  watch: tail -f logs/logging.out"
echo "  stop : ./logging.sh stop"
