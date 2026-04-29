#!/bin/bash
# ═══════════════════════════════════════════════════════
# STT vLLM 클러스터 시작/중지/상태 스크립트
#
# configs/ 아래 모든 *.yaml 을 순회하여 각각 vLLM 인스턴스를 기동한다.
# 각 config가 자체적으로 model / gpus / port 를 정의한다.
# 런처는 vllm/vllm_server_launcher.py 를 그대로 재사용 (모델 다운로드 + 오프라인 모드 + 임시 config 처리 자산 포함).
#
# 사용법:
#   ./start.sh              # 전체 STT 인스턴스 기동
#   ./start.sh stop         # 전체 중지
#   ./start.sh status       # 상태 확인
#   ./start.sh restart      # 전체 재시작
# ═══════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
CONFIGS_DIR="$SCRIPT_DIR/configs"
LAUNCHER="$SCRIPT_DIR/../vllm/vllm_server_launcher.py"

mkdir -p "$LOG_DIR"

if [ ! -f "$LAUNCHER" ]; then
    echo "ERROR: launcher 없음: $LAUNCHER" >&2
    exit 1
fi

# ── Config 파싱 ──────────────────────────────────────
# configs/*.yaml 각각에서 name/config/gpus/port 추출.
# 파일명을 인스턴스 식별자로 사용 (예: qwen3_asr.yaml → qwen3_asr).
eval "$(python3 - "$CONFIGS_DIR" <<'PYEOF'
import glob, os, sys, yaml

configs_dir = sys.argv[1]
files = sorted(glob.glob(os.path.join(configs_dir, "*.yaml")))
if not files:
    print('echo "ERROR: configs/*.yaml 없음" >&2; exit 1')
    sys.exit(0)

print(f"INSTANCE_COUNT={len(files)}")
for i, path in enumerate(files):
    with open(path) as f:
        c = yaml.safe_load(f) or {}
    name = os.path.splitext(os.path.basename(path))[0]
    gpus = c.get("gpus", [0])
    port = c.get("port", 7170 + i)
    cuda = ",".join(str(g) for g in gpus)
    print(f"INST_NAME_{i}='{name}'")
    print(f"INST_CONFIG_{i}='{path}'")
    print(f"INST_GPUS_{i}='{cuda}'")
    print(f"INST_PORT_{i}={port}")
PYEOF
)"

inst_name()   { eval "echo \$INST_NAME_$1"; }
inst_config() { eval "echo \$INST_CONFIG_$1"; }
inst_gpus()   { eval "echo \$INST_GPUS_$1"; }
inst_port()   { eval "echo \$INST_PORT_$1"; }

is_running() {
    curl -so /dev/null --connect-timeout 1 "http://127.0.0.1:$1/health" 2>/dev/null
}

get_pid() {
    netstat -tlnp 2>/dev/null | awk -v port=":$1" '$4 ~ port {split($7,a,"/"); print a[1]}' || true
}

cmd_start() {
    echo "═══ STT vLLM 인스턴스 시작 ═══"
    echo "  인스턴스: ${INSTANCE_COUNT}개"
    echo ""

    for ((i=0; i<INSTANCE_COUNT; i++)); do
        name=$(inst_name $i)
        cfg=$(inst_config $i)
        gpus=$(inst_gpus $i)
        port=$(inst_port $i)

        if is_running "$port"; then
            echo "[SKIP]  $name (GPU $gpus :$port) — 이미 실행 중"
            continue
        fi

        echo "[START] $name (GPU $gpus :$port)"
        echo "        config: $cfg"
        # 각 config가 자체 port 를 명시하므로 --port 추가 전달은 'duplicate keys --port'
        # 경고를 유발한다. 포트는 config 만 신뢰.
        nohup python "$LAUNCHER" -c "$cfg" -g "$gpus" \
            > "$LOG_DIR/$name.log" 2>&1 &
        echo "        PID $!, 로그: logs/$name.log"
    done

    echo ""
    echo "상태 확인: ./start.sh status"
    echo "모델 목록: curl http://localhost:<port>/v1/models"
}

cmd_stop() {
    echo "═══ STT vLLM 인스턴스 중지 ═══"
    for ((i=0; i<INSTANCE_COUNT; i++)); do
        name=$(inst_name $i)
        port=$(inst_port $i)
        pid=$(get_pid "$port")
        if [ -n "$pid" ]; then
            echo "[STOP]  $name (:$port) PID $pid"
            kill "$pid" 2>/dev/null || true
        else
            echo "[SKIP]  $name (:$port) — 실행 중 아님"
        fi
    done
    echo ""
    echo "완료"
}

cmd_status() {
    echo "═══ STT vLLM 인스턴스 상태 ═══"
    for ((i=0; i<INSTANCE_COUNT; i++)); do
        name=$(inst_name $i)
        gpus=$(inst_gpus $i)
        port=$(inst_port $i)
        if is_running "$port"; then
            echo "[UP]   $name (GPU $gpus :$port)"
        else
            echo "[DOWN] $name (GPU $gpus :$port)"
        fi
    done
}

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_stop; echo ""; sleep 2; cmd_start ;;
    *)       echo "사용법: $0 {start|stop|status|restart}"; exit 1 ;;
esac
