#!/bin/bash
# ═══════════════════════════════════════════════════════
# vLLM 클러스터 시작/중지/상태 스크립트
#
# 모든 설정은 vllm_config.yaml, vllm_gateway_config.yaml에서 관리.
# 이 스크립트는 config를 읽고 실행만 한다.
#
# 사용법:
#   ./start.sh              # 전체 기동
#   ./start.sh stop         # 전체 중지
#   ./start.sh status       # 상태 확인
#   ./start.sh restart      # 전체 재시작
# ═══════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# ── Config 파싱 ──────────────────────────────────────
# gpus를 tensor_parallel_size 단위로 그룹핑하여 인스턴스 목록 생성.
# 예) gpus: [0,1,2,3], tp: 2 → 인스턴스 2개: GPU(0,1):7070, GPU(2,3):7071
eval "$(python3 - "$SCRIPT_DIR" <<'PYEOF'
import yaml, sys

d = sys.argv[1]
vc = yaml.safe_load(open(f"{d}/vllm_config.yaml"))
gc = yaml.safe_load(open(f"{d}/vllm_gateway_config.yaml"))

gpus = vc.get("gpus", [0])
tp = vc.get("tensor_parallel_size", 1)
base_port = vc.get("port", 7070)
gw_port = gc["gateway"]["port"]

if len(gpus) % tp != 0:
    print(f'echo "ERROR: gpus({len(gpus)}개)가 tensor_parallel_size({tp})로 나누어지지 않습니다" >&2; exit 1')
    sys.exit(0)

n = len(gpus) // tp
print(f"INSTANCE_COUNT={n}")
print(f"BASE_PORT={base_port}")
print(f"GATEWAY_PORT={gw_port}")
for i in range(n):
    group = gpus[i * tp:(i + 1) * tp]
    cuda_devs = ",".join(str(g) for g in group)
    label = "_".join(str(g) for g in group)
    print(f"INST_GPUS_{i}='{cuda_devs}'")
    print(f"INST_PORT_{i}={base_port + i}")
    print(f"INST_LABEL_{i}='{label}'")
PYEOF
)"

# Helper: 인스턴스 i의 값 읽기
inst_gpus()  { eval "echo \$INST_GPUS_$1"; }
inst_port()  { eval "echo \$INST_PORT_$1"; }
inst_label() { eval "echo \$INST_LABEL_$1"; }

is_running() {
    curl -so /dev/null --connect-timeout 1 "http://127.0.0.1:$1/health" 2>/dev/null
}

get_pid() {
    netstat -tlnp 2>/dev/null | awk -v port=":$1" '$4 ~ port {split($7,a,"/"); print a[1]}' || true
}

cmd_start() {
    echo "═══ vLLM 클러스터 시작 ═══"
    echo "  인스턴스: ${INSTANCE_COUNT}개, base_port: $BASE_PORT, gateway: :$GATEWAY_PORT"
    echo ""

    # vLLM 인스턴스 기동
    for ((i=0; i<INSTANCE_COUNT; i++)); do
        gpus=$(inst_gpus $i)
        port=$(inst_port $i)
        label=$(inst_label $i)

        if is_running "$port"; then
            echo "[SKIP]  vLLM GPU $gpus (:$port) — 이미 실행 중"
            continue
        fi

        echo "[START] vLLM GPU $gpus (:$port)"
        # i=0이고 port==BASE_PORT면 config 값과 동일하므로 --port 전달 시
        # vllm serve가 'Found duplicate keys --port' 경고를 낸다. 중복 방지를 위해
        # 오버라이드가 필요한 인스턴스(i>0)에만 --port를 전달한다.
        port_arg=()
        if (( i > 0 )); then
            port_arg=(--port "$port")
        fi
        nohup python "$SCRIPT_DIR/vllm_server_launcher.py" -g "$gpus" "${port_arg[@]}" \
            > "$LOG_DIR/vllm_gpu${label}.log" 2>&1 &
        echo "        PID $!, 로그: logs/vllm_gpu${label}.log"
    done

    # 게이트웨이 기동
    echo ""
    if is_running "$GATEWAY_PORT"; then
        echo "[SKIP]  Gateway (:$GATEWAY_PORT) — 이미 실행 중"
    else
        echo "[START] Gateway (:$GATEWAY_PORT)"
        nohup python "$SCRIPT_DIR/vllm_gateway.py" \
            > /dev/null 2>&1 &
        echo "        PID $!, 로그: logs/gateway.log"
    fi

    echo ""
    echo "상태 확인: ./start.sh status"
    echo "서버 상태: curl http://localhost:$GATEWAY_PORT/server-status"
}

cmd_stop() {
    echo "═══ vLLM 클러스터 중지 ═══"

    # 게이트웨이 먼저 중지
    local pid
    pid=$(get_pid "$GATEWAY_PORT")
    if [ -n "$pid" ]; then
        echo "[STOP]  Gateway (:$GATEWAY_PORT) PID $pid"
        kill "$pid" 2>/dev/null || true
    else
        echo "[SKIP]  Gateway (:$GATEWAY_PORT) — 실행 중 아님"
    fi

    # vLLM 인스턴스 중지
    for ((i=0; i<INSTANCE_COUNT; i++)); do
        gpus=$(inst_gpus $i)
        port=$(inst_port $i)
        pid=$(get_pid "$port")
        if [ -n "$pid" ]; then
            echo "[STOP]  vLLM GPU $gpus (:$port) PID $pid"
            kill "$pid" 2>/dev/null || true
        else
            echo "[SKIP]  vLLM GPU $gpus (:$port) — 실행 중 아님"
        fi
    done

    echo ""
    echo "완료"
}

cmd_status() {
    echo "═══ vLLM 클러스터 상태 ═══"

    for ((i=0; i<INSTANCE_COUNT; i++)); do
        gpus=$(inst_gpus $i)
        port=$(inst_port $i)
        if is_running "$port"; then
            echo "[UP]   vLLM GPU $gpus (:$port)"
        else
            echo "[DOWN] vLLM GPU $gpus (:$port)"
        fi
    done

    echo ""
    if is_running "$GATEWAY_PORT"; then
        echo "[UP]   Gateway (:$GATEWAY_PORT)"
    else
        echo "[DOWN] Gateway (:$GATEWAY_PORT)"
    fi
}

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_stop; echo ""; sleep 2; cmd_start ;;
    *)       echo "사용법: $0 {start|stop|status|restart}"; exit 1 ;;
esac
