#!/usr/bin/env bash
# PII 가드 기동 — NER 서버(GPU3) + 프록시(포트별)
#
# 사용법:
#   ./start.sh up            # NER + 연구계 gemma 프록시(5015)  (기본)
#   ./start.sh up 5015       # NER + 연구계 gemma 프록시(5015)
#   ./start.sh up 5016       # NER + 연구계 qwen  프록시(5016)
#   ./start.sh up 5501       # NER + 운영계 gemma 프록시(5501)
#   ./start.sh up 5502       # NER + 운영계 qwen  프록시(5502)
#   ./start.sh up all        # (단일 호스트 공존 시) NER + 등록된 프록시 전부 — 격리 운영은 각 서버서 자기 포트만
#   ./start.sh down [port|all]   # 프록시 종료(all 이면 NER 포함 전부)
#   ./start.sh status        # health 확인
#
# 토폴로지: PII 프록시(외부 5015·5016/5501·5502) → 게이트웨이(내부 6015·6016/6501·6502) → vLLM.
#   같은 서버의 gemma·qwen 프록시는 같은 NER 풀(8911/8901)을 공유한다(NER은 한 번만 기동).
# NER 서버는 token-classification(vLLM 비대상)이라 transformers로 GPU3에 서빙한다.
# 연구계/운영계는 격리된 별도 서버다. 각 서버가 자기 localhost(8911/8901)에 NER을
# 띄우고 자기 포트 프록시만 바라본다(연구↔운영 NER은 물리 분리, 공유 아님).
# 모델은 /models/PII 에 위치. 프록시 설정은 configs/proxy.yaml(5015)·proxy.5016.yaml(5016)·proxy.5501.yaml(5501)·proxy.5502.yaml(5502).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODELS="${PII_MODELS_DIR:-/models/PII}"
GPU="${PII_GPU:-3}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# 포트 → 프록시 설정 매핑 (외부 진입 포트 기준)
declare -A PROXY_CONFIGS=(
    [5015]="$SCRIPT_DIR/configs/proxy.yaml"          # 연구계 gemma
    [5016]="$SCRIPT_DIR/configs/proxy.5016.yaml"     # 연구계 qwen
    [5501]="$SCRIPT_DIR/configs/proxy.5501.yaml"     # 운영계 gemma
    [5502]="$SCRIPT_DIR/configs/proxy.5502.yaml"     # 운영계 qwen
)

# 백엔드 정의: "tag|port|model_path"
NER_BACKENDS=(
    "vmaca123|8911|$MODELS/vmaca123/korean-pii-ner-v3"
    "townboy|8901|$MODELS/townboy/kpfbert-kdpii"
)

# ── 감사로그 salt 주입 (평문 미저장 지문용) ───────────
# 우선순위: env PII_AUDIT_SALT > configs/audit.salt 파일 > (없으면 자동 생성).
# salt가 비면 audit.py가 'NOSALT'로 동작(지문 비활성). 생성은 umask 077로 600 권한.
SALT_FILE="${PII_SALT_FILE:-$SCRIPT_DIR/configs/audit.salt}"
load_salt() {
    if [ -n "${PII_AUDIT_SALT:-}" ]; then
        echo "[SALT]  env PII_AUDIT_SALT 사용"
        return
    fi
    if [ ! -f "$SALT_FILE" ]; then
        if command -v openssl > /dev/null 2>&1; then
            ( umask 077; openssl rand -hex 32 > "$SALT_FILE" )
            echo "[SALT]  신규 생성: $SALT_FILE (권한 600)"
        else
            echo "[WARN]  openssl 없음 + salt 미설정 → NOSALT(지문 비활성). configs/audit.salt 수동 생성 권장"
            return
        fi
    fi
    export PII_AUDIT_SALT="$(cat "$SALT_FILE")"
    echo "[SALT]  $SALT_FILE 로드 완료"
}

start_ner() {
    for spec in "${NER_BACKENDS[@]}"; do
        IFS='|' read -r tag port path <<< "$spec"
        if curl -sf "http://127.0.0.1:$port/health" > /dev/null 2>&1; then
            echo "[SKIP]  NER $tag (:$port) 이미 실행 중"
            continue
        fi
        echo "[START] NER $tag (:$port, GPU$GPU) ← $path"
        CUDA_VISIBLE_DEVICES="$GPU" nohup python ner_server.py \
            --model-path "$path" --model-tag "$tag" --port "$port" --device cuda \
            > "$LOG_DIR/ner_${tag}.log" 2>&1 &
        echo "        PID $!, 로그: logs/ner_${tag}.log"
    done
}

start_proxy() {
    local port="$1" config="$2"
    if [ ! -f "$config" ]; then
        echo "[ERR]   프록시 (:$port) 설정 없음: $config"
        return 1
    fi
    if curl -sf "http://127.0.0.1:$port/health" > /dev/null 2>&1; then
        echo "[SKIP]  프록시 (:$port) 이미 실행 중"
        return
    fi
    echo "[START] PII 프록시 (:$port) ← $config"
    nohup python proxy.py -c "$config" > "$LOG_DIR/proxy_${port}.log" 2>&1 &
    echo "        PID $!, 로그: logs/proxy_${port}.log"
}

stop_proxy() {
    local port="$1" config="$2"
    # proxy.py -c <config> 패턴으로 정밀 종료. 이 down 명령(start.sh) 자신은
    # 패턴(proxy.py)에 매치되지 않아 self-kill 위험 없음.
    if pkill -f "proxy.py -c $config"; then
        echo "[STOP]  프록시 (:$port)"
    else
        echo "[SKIP]  프록시 (:$port) 실행 중 아님"
    fi
}

stop_ner() {
    pkill -f "ner_server.py --model-path $MODELS" && echo "[STOP]  NER 서버(GPU$GPU)" \
        || echo "[SKIP]  NER 서버 실행 중 아님"
}

# 대상 포트 목록 결정: 인자 없음→5015, 'all'→전체, 숫자→해당.
resolve_ports() {
    local target="${1:-5015}"
    if [ "$target" = "all" ]; then
        printf '%s\n' "${!PROXY_CONFIGS[@]}" | sort
    elif [ -n "${PROXY_CONFIGS[$target]:-}" ]; then
        echo "$target"
    else
        echo "ERR_UNKNOWN_PORT"
    fi
}

cmd_up() {
    local target="${1:-5015}"
    local ports; ports=$(resolve_ports "$target")
    if [ "$ports" = "ERR_UNKNOWN_PORT" ]; then
        echo "ERROR: 알 수 없는 포트 '$target'. 지원: ${!PROXY_CONFIGS[*]} | all" >&2
        exit 1
    fi
    load_salt
    start_ner
    echo "[WAIT]  NER 모델 로딩 대기..."
    sleep 8
    while IFS= read -r port; do
        [ -z "$port" ] && continue
        start_proxy "$port" "${PROXY_CONFIGS[$port]}"
    done <<< "$ports"
    echo "[DONE]  ./start.sh status 로 확인"
}

cmd_down() {
    local target="${1:-all}"
    if [ "$target" = "all" ]; then
        for port in $(printf '%s\n' "${!PROXY_CONFIGS[@]}" | sort); do
            stop_proxy "$port" "${PROXY_CONFIGS[$port]}"
        done
        stop_ner
        return
    fi
    local ports; ports=$(resolve_ports "$target")
    if [ "$ports" = "ERR_UNKNOWN_PORT" ]; then
        echo "ERROR: 알 수 없는 포트 '$target'. 지원: ${!PROXY_CONFIGS[*]} | all" >&2
        exit 1
    fi
    stop_proxy "$target" "${PROXY_CONFIGS[$target]}"
    echo "[INFO]  NER은 로딩 비용이 커 유지(프록시만 재기동 시). 전체 종료는 './start.sh down all'"
}

cmd_status() {
    for spec in "${NER_BACKENDS[@]}"; do
        IFS='|' read -r tag port _ <<< "$spec"
        code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/health" 2>/dev/null)
        echo "NER $tag (:$port): ${code:-down}"
    done
    for port in $(printf '%s\n' "${!PROXY_CONFIGS[@]}" | sort); do
        code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/health" 2>/dev/null)
        echo "프록시 (:$port): ${code:-down}"
    done
}

case "${1:-}" in
    up)     shift || true; cmd_up "${1:-}" ;;
    down)   shift || true; cmd_down "${1:-}" ;;
    status) cmd_status ;;
    *)      echo "사용법: $0 {up [port|all] | down [port|all] | status}  (port: ${!PROXY_CONFIGS[*]})"; exit 1 ;;
esac
