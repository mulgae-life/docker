#!/bin/bash
# ═══════════════════════════════════════════════════════
# vLLM 클러스터 시작/중지/상태 스크립트
#
# 디렉토리 규약:
#   instances/<name>.yaml   — vLLM 인스턴스 1대 정의 (port, gpus, model, gateway_port)
#   gateways/<port>.yaml    — 게이트웨이 1대 정의 (gateway.port, discover_from)
#
# 게이트웨이는 instances/*.yaml 중 gateway_port == 자기 포트인 것을
# 자동으로 backends에 등록한다(vllm_gateway.py의 discover_from).
#
# 사용법:
#   ./start.sh up                # 전체 인스턴스 + 모든 게이트웨이 기동
#   ./start.sh up gemma          # 단일 인스턴스만 기동 (instances/gemma.yaml)
#   ./start.sh up qwen           # 단일 인스턴스만 기동 (instances/qwen.yaml)
#   ./start.sh down              # 전체 중지 (게이트웨이 → 인스턴스 순)
#   ./start.sh down gemma        # 단일 인스턴스만 중지
#   ./start.sh status            # 상태 확인
#   ./start.sh restart [name]    # 재시작
#
# 하위 호환:
#   ./start.sh start = up,  ./start.sh stop = down
# ═══════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTANCES_DIR="$SCRIPT_DIR/instances"
GATEWAYS_DIR="$SCRIPT_DIR/gateways"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# ── 인스턴스/게이트웨이 yaml 파싱 ────────────────────
# instances/*.yaml에서 (name, port, gpus_csv, gateway_port) 추출
# gateways/*.yaml에서 (port) 추출
parse_instance_yaml() {
    local yaml_path="$1"
    python3 - "$yaml_path" <<'PYEOF'
import sys, yaml, os
path = sys.argv[1]
data = yaml.safe_load(open(path)) or {}
name = os.path.splitext(os.path.basename(path))[0]
port = data.get("port", "")
gpus = data.get("gpus", [])
gpus_csv = ",".join(str(g) for g in gpus)
gateway_port = data.get("gateway_port", "")
# 셸 평가용 변수
print(f"INST_NAME={name}")
print(f"INST_PORT={port}")
print(f"INST_GPUS_CSV={gpus_csv}")
print(f"INST_GATEWAY_PORT={gateway_port}")
PYEOF
}

parse_gateway_yaml() {
    local yaml_path="$1"
    python3 - "$yaml_path" <<'PYEOF'
import sys, yaml, os
path = sys.argv[1]
data = yaml.safe_load(open(path)) or {}
name = os.path.splitext(os.path.basename(path))[0]
port = data.get("gateway", {}).get("port", "")
print(f"GW_NAME={name}")
print(f"GW_PORT={port}")
PYEOF
}

is_running() {
    curl -so /dev/null --connect-timeout 1 "http://127.0.0.1:$1/health" 2>/dev/null
}

get_pid() {
    netstat -tlnp 2>/dev/null | awk -v port=":$1" '$4 ~ port {split($7,a,"/"); print a[1]}' || true
}

list_instance_yamls() {
    # 단일 인자(name) 주어지면 그것만, 아니면 전체 *.yaml
    local target="${1:-}"
    if [ -n "$target" ]; then
        local path="$INSTANCES_DIR/${target}.yaml"
        if [ ! -f "$path" ]; then
            echo "ERROR: 인스턴스 yaml 없음: $path" >&2
            return 1
        fi
        echo "$path"
    else
        ls "$INSTANCES_DIR"/*.yaml 2>/dev/null | sort
    fi
}

list_gateway_yamls() {
    ls "$GATEWAYS_DIR"/*.yaml 2>/dev/null | sort
}

# ── 명령 구현 ─────────────────────────────────────────

start_instance() {
    local yaml_path="$1"
    eval "$(parse_instance_yaml "$yaml_path")"

    if [ -z "$INST_PORT" ]; then
        echo "[SKIP]  $INST_NAME — port 키 없음 ($yaml_path)"
        return
    fi

    if is_running "$INST_PORT"; then
        echo "[SKIP]  vLLM $INST_NAME (GPU $INST_GPUS_CSV, :$INST_PORT) — 이미 실행 중"
        return
    fi

    echo "[START] vLLM $INST_NAME (GPU $INST_GPUS_CSV, :$INST_PORT, → gateway :$INST_GATEWAY_PORT)"
    nohup python "$SCRIPT_DIR/vllm_server_launcher.py" \
        -c "$yaml_path" \
        -g "$INST_GPUS_CSV" \
        > "$LOG_DIR/vllm_${INST_NAME}.log" 2>&1 &
    echo "        PID $!, 로그: logs/vllm_${INST_NAME}.log"
}

stop_instance() {
    local yaml_path="$1"
    eval "$(parse_instance_yaml "$yaml_path")"

    if [ -z "$INST_PORT" ]; then
        return
    fi

    local pid
    pid=$(get_pid "$INST_PORT")
    if [ -n "$pid" ]; then
        echo "[STOP]  vLLM $INST_NAME (:$INST_PORT) PID $pid"
        kill "$pid" 2>/dev/null || true
    else
        echo "[SKIP]  vLLM $INST_NAME (:$INST_PORT) — 실행 중 아님"
    fi
}

start_gateway() {
    local yaml_path="$1"
    eval "$(parse_gateway_yaml "$yaml_path")"

    if [ -z "$GW_PORT" ]; then
        echo "[SKIP]  gateway $GW_NAME — gateway.port 없음"
        return
    fi

    if is_running "$GW_PORT"; then
        echo "[SKIP]  Gateway $GW_NAME (:$GW_PORT) — 이미 실행 중"
        return
    fi

    echo "[START] Gateway $GW_NAME (:$GW_PORT)"
    nohup python "$SCRIPT_DIR/vllm_gateway.py" -c "$yaml_path" \
        > "$LOG_DIR/gateway_${GW_NAME}.log" 2>&1 &
    echo "        PID $!, 로그: logs/gateway_${GW_NAME}.log"
}

stop_gateway() {
    local yaml_path="$1"
    eval "$(parse_gateway_yaml "$yaml_path")"

    if [ -z "$GW_PORT" ]; then
        return
    fi

    local pid
    pid=$(get_pid "$GW_PORT")
    if [ -n "$pid" ]; then
        echo "[STOP]  Gateway $GW_NAME (:$GW_PORT) PID $pid"
        kill "$pid" 2>/dev/null || true
    else
        echo "[SKIP]  Gateway $GW_NAME (:$GW_PORT) — 실행 중 아님"
    fi
}

cmd_up() {
    local target="${1:-}"
    echo "═══ vLLM 클러스터 시작 ═══"
    if [ -n "$target" ]; then
        echo "  대상: 인스턴스 '$target' 단일 (게이트웨이는 별도)"
    fi
    echo ""

    # 1) 인스턴스 기동
    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        start_instance "$yaml_path"
    done < <(list_instance_yamls "$target")

    # 2) 단일 인스턴스 모드면 게이트웨이는 건드리지 않음 (이미 떠있는 게이트웨이가 자동 감지)
    if [ -n "$target" ]; then
        echo ""
        echo "단일 인스턴스 모드 — 게이트웨이는 기존 상태 유지 (HealthChecker가 자동 감지)"
        return
    fi

    # 3) 전체 모드: 게이트웨이도 모두 기동
    echo ""
    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        start_gateway "$yaml_path"
    done < <(list_gateway_yamls)

    echo ""
    echo "상태 확인: ./start.sh status"
}

cmd_down() {
    local target="${1:-}"
    echo "═══ vLLM 클러스터 중지 ═══"
    echo ""

    # 1) 게이트웨이 먼저 중지 (전체 모드만)
    if [ -z "$target" ]; then
        while IFS= read -r yaml_path; do
            [ -z "$yaml_path" ] && continue
            stop_gateway "$yaml_path"
        done < <(list_gateway_yamls)
        echo ""
    fi

    # 2) 인스턴스 중지
    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        stop_instance "$yaml_path"
    done < <(list_instance_yamls "$target")

    echo ""
    echo "완료"
}

cmd_status() {
    echo "═══ vLLM 클러스터 상태 ═══"

    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        eval "$(parse_instance_yaml "$yaml_path")"
        if is_running "$INST_PORT"; then
            echo "[UP]   vLLM $INST_NAME (GPU $INST_GPUS_CSV, :$INST_PORT, → gw :$INST_GATEWAY_PORT)"
        else
            echo "[DOWN] vLLM $INST_NAME (GPU $INST_GPUS_CSV, :$INST_PORT, → gw :$INST_GATEWAY_PORT)"
        fi
    done < <(list_instance_yamls)

    echo ""
    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        eval "$(parse_gateway_yaml "$yaml_path")"
        if is_running "$GW_PORT"; then
            echo "[UP]   Gateway $GW_NAME (:$GW_PORT)"
        else
            echo "[DOWN] Gateway $GW_NAME (:$GW_PORT)"
        fi
    done < <(list_gateway_yamls)
}

cmd_restart() {
    local target="${1:-}"
    cmd_down "$target"
    echo ""
    sleep 2
    cmd_up "$target"
}

case "${1:-up}" in
    up|start)     shift || true; cmd_up "${1:-}" ;;
    down|stop)    shift || true; cmd_down "${1:-}" ;;
    status)       cmd_status ;;
    restart)      shift || true; cmd_restart "${1:-}" ;;
    *)            echo "사용법: $0 {up|down|status|restart} [name]"; exit 1 ;;
esac
