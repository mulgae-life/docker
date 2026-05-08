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
# 일자 분할:
#   vllm 로그 안의 'INFO MM-DD ...' 라인(throughput 등 10초 주기)에서 일자를 추출하여
#   호출 라인을 그 시점 일자 키로 라우팅. 즉 첫 sync 시 누적된 며칠치 백로그도
#   원래 일자별로 자동 분배되며, 자정 경계 5분 chunk도 정확히 양일에 나뉘어 들어감.
#
# ⚠ 운영 안전성:
#   vLLM 로그 파일을 read-only(stat / tail -c / awk)로 읽기만 합니다.
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
        STATE_MD="logs/.${INST}.last_md"
        STATE_YEAR="logs/.${INST}.last_year"
        DIRTY="logs/.${INST}.dirty"
        SYNC_DIR="logs/sync"

        if [ ! -f "$SRC" ]; then
            echo "[$(date '+%F %T')] skip — $SRC 없음"
            sleep "$INTERVAL"
            continue
        fi

        mkdir -p "$SYNC_DIR"
        prev="$(cat "$STATE" 2>/dev/null || echo 0)"
        size="$(stat -c %s "$SRC")"
        [ "$size" -lt "$prev" ] && prev=0    # vllm 재기동 truncate 감지
        : > "$DIRTY"

        if [ "$size" -gt "$prev" ]; then
            # vllm 로그 안의 'INFO MM-DD ...' 라인을 보고 그 시점 일자에 해당하는
            # 파일로 호출 라인을 라우팅. 일자 변경 시점에서 자동 분할.
            last_md="$(cat "$STATE_MD" 2>/dev/null || echo "")"
            last_year="$(cat "$STATE_YEAR" 2>/dev/null || date +%Y)"

            tail -c +$((prev + 1)) "$SRC" | awk \
                -v inst="$INST" \
                -v init_md="$last_md" \
                -v init_year="$last_year" \
                -v out_dir="$SYNC_DIR" \
                -v state_md="$STATE_MD" \
                -v state_year="$STATE_YEAR" \
                -v dirty_file="$DIRTY" '
                BEGIN { cur_md = init_md; cur_year = init_year }
                {
                    if (match($0, /INFO [0-9][0-9]-[0-9][0-9] /)) {
                        new_md = substr($0, RSTART+5, 5)
                        if (cur_md == "12-31" && new_md == "01-01") cur_year++
                        cur_md = new_md
                    }
                }
                /POST \/v1\// {
                    if (cur_md != "") {
                        key = cur_year "-" cur_md
                        print $0 >> (out_dir "/" inst "-" key ".log")
                        dirty[key] = 1
                    }
                }
                END {
                    if (cur_md != "") {
                        print cur_md > state_md
                        print cur_year > state_year
                    }
                    for (k in dirty) print k > dirty_file
                }
            '
        fi
        echo "$size" > "$STATE"

        # 변경된 일자 파일만 S3로 업로드 (보통 1개, 자정 경계엔 2개)
        uploaded=0
        if [ -s "$DIRTY" ]; then
            while IFS= read -r date_part; do
                f="$SYNC_DIR/${INST}-${date_part}.log"
                [ -f "$f" ] && aws s3 cp "$f" \
                    "s3://${S3_BUCKET}/${S3_PREFIX}/${INST}/${date_part}.log" --only-show-errors \
                    && uploaded=$((uploaded + 1))
            done < "$DIRTY"
        fi
        echo "[$(date '+%F %T')] sync $INST — $uploaded date(s) uploaded"

        sleep "$INTERVAL"
    done
) >> logs/logging.out 2>&1 &

echo $! > "$PID"
disown
echo "started (PID $!) → s3://${S3_BUCKET}/${S3_PREFIX}/${INST}/, interval=${INTERVAL}s"
echo "  watch: tail -f logs/logging.out"
echo "  stop : ./logging.sh stop"
