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
# 사용법 ([name]은 인스턴스/게이트웨이 yaml 파일명에서 자동 감지):
#   ./start.sh up                # 전체 인스턴스 + 모든 게이트웨이 기동
#   ./start.sh up gemma          # instances/gemma.yaml 단독 기동 (게이트웨이 미터치)
#   ./start.sh up 5015           # gateways/5015.yaml 단독 기동 (인스턴스 미터치)
#   ./start.sh down              # 전체 중지 (게이트웨이 → 인스턴스 순)
#   ./start.sh down qwen         # instances/qwen.yaml 단독 중지
#   ./start.sh down 5016         # gateways/5016.yaml 단독 중지
#   ./start.sh status            # 상태 확인
#   ./start.sh restart [name]    # 재시작 ([name] 동일 자동 감지)
#
# 라우팅 규칙:
#   [name]이 instances/<name>.yaml로 존재 → 인스턴스 명령
#   [name]이 gateways/<name>.yaml로 존재 → 게이트웨이 명령
#   둘 다 없으면 즉시 에러
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
import sys, yaml, os, json
path = sys.argv[1]
data = yaml.safe_load(open(path)) or {}
name = os.path.splitext(os.path.basename(path))[0]
yaml_port = data.get("port", "")
gpus = data.get("gpus", [])
gpus_csv = ",".join(str(g) for g in gpus)
gateway_port = data.get("gateway_port", "")

# 실제 사용 중인 port: instances/.runtime/<name>.json 우선 (launcher 자동 회피 결과).
# 없으면 yaml의 port hint를 fallback으로 사용 (인스턴스 미기동 상태).
runtime_path = os.path.join(os.path.dirname(path), ".runtime", f"{name}.json")
actual_port = yaml_port
port_source = "yaml"
if os.path.isfile(runtime_path):
    try:
        with open(runtime_path) as f:
            rt = json.load(f)
        if "port" in rt:
            actual_port = rt["port"]
            port_source = "runtime"
    except (OSError, json.JSONDecodeError):
        pass

# 셸 평가용 변수
print(f"INST_NAME={name}")
print(f"INST_PORT={actual_port}")
print(f"INST_PORT_HINT={yaml_port}")
print(f"INST_PORT_SOURCE={port_source}")
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

# runtime json에서 pid 추출. 파일/키 부재·파싱 실패 시 빈 출력.
read_runtime_pid() {
    local runtime_path="$1"
    [ -f "$runtime_path" ] || return 0
    python3 -c "import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print(d.get('pid','') or '')
except Exception:
    pass" "$runtime_path" 2>/dev/null
}

# PID 생사 검사. 두번째 인자(cmdline 패턴) 주면 /proc/<pid>/cmdline 매칭까지 확인.
# 컨테이너 재시작·long-running 환경에서 PID 재사용된 다른 프로세스를 잘못 잡는 사고 방어.
is_pid_alive() {
    local pid="${1:-}"
    local pattern="${2:-}"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [ -z "$pattern" ] && return 0
    [ -r "/proc/$pid/cmdline" ] || return 1
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q -- "$pattern"
}

# launcher가 runtime json을 쓸 때까지 폴링. 게이트웨이 디스커버리가 yaml port hint 대신
# 자동 회피된 실제 port를 잡도록 보장 (up all 경합 해소).
# 시간 초과 시 1 반환 — 호출자가 경고 출력.
wait_runtime_ready() {
    local runtime_path="$1"
    local timeout="${2:-30}"
    local i
    for i in $(seq 1 "$timeout"); do
        [ -f "$runtime_path" ] && return 0
        sleep 1
    done
    return 1
}

list_instance_yamls() {
    ls "$INSTANCES_DIR"/*.yaml 2>/dev/null | sort
}

list_gateway_yamls() {
    ls "$GATEWAYS_DIR"/*.yaml 2>/dev/null | sort
}

# [name]을 instances/ 또는 gateways/로 라우팅. 결과: "all" | "instance" | "gateway"
# 매칭 실패 시 stderr 에러 메시지 + return 1.
detect_target_kind() {
    local target="$1"
    if [ -z "$target" ]; then
        echo "all"; return 0
    fi
    local inst_path="$INSTANCES_DIR/${target}.yaml"
    local gw_path="$GATEWAYS_DIR/${target}.yaml"
    if [ -f "$inst_path" ] && [ -f "$gw_path" ]; then
        echo "ERROR: '$target'이 instances/와 gateways/ 양쪽에 존재합니다. 파일명 충돌." >&2
        return 1
    fi
    if [ -f "$inst_path" ]; then
        echo "instance"; return 0
    fi
    if [ -f "$gw_path" ]; then
        echo "gateway"; return 0
    fi
    echo "ERROR: '$target' — instances/${target}.yaml 또는 gateways/${target}.yaml 없음" >&2
    echo "  인스턴스: $(ls "$INSTANCES_DIR"/*.yaml 2>/dev/null | xargs -n1 basename | sed 's/\.yaml$//' | tr '\n' ' ')" >&2
    echo "  게이트웨이: $(ls "$GATEWAYS_DIR"/*.yaml 2>/dev/null | xargs -n1 basename | sed 's/\.yaml$//' | tr '\n' ' ')" >&2
    return 1
}

# ── 명령 구현 ─────────────────────────────────────────

start_instance() {
    local yaml_path="$1"
    eval "$(parse_instance_yaml "$yaml_path")"

    if [ -z "$INST_PORT_HINT" ]; then
        echo "[SKIP]  $INST_NAME — port 키 없음 ($yaml_path)"
        return
    fi

    # 인스턴스 정체성 = instances/.runtime/<name>.json + 그 안의 launcher PID 생존.
    # 같은 yaml port를 갖는 다른 인스턴스가 그 포트를 잡고 있어도, 자기 이름의
    # runtime 파일이 없으면 새로 기동 가능 (launcher가 +1 자동 회피).
    # PID가 죽었거나 다른 프로세스로 재사용되었으면 stale로 보고 자동 정리.
    local runtime_path="$INSTANCES_DIR/.runtime/${INST_NAME}.json"
    if [ -f "$runtime_path" ]; then
        local existing_pid
        existing_pid=$(read_runtime_pid "$runtime_path")
        if is_pid_alive "$existing_pid" "vllm_server_launcher.py"; then
            echo "[SKIP]  vLLM $INST_NAME — 이미 실행 중 (PID $existing_pid)"
            return
        fi
        echo "[CLEAN] vLLM $INST_NAME — stale runtime 정리 (PID ${existing_pid:-?} launcher 아님/죽음)"
        rm -f "$runtime_path"
    fi

    echo "[START] vLLM $INST_NAME (GPU $INST_GPUS_CSV, port hint :$INST_PORT_HINT, → gateway :$INST_GATEWAY_PORT)"
    nohup python "$SCRIPT_DIR/vllm_server_launcher.py" \
        -c "$yaml_path" \
        -g "$INST_GPUS_CSV" \
        > "$LOG_DIR/vllm_${INST_NAME}.log" 2>&1 &
    echo "        PID $!, 로그: logs/vllm_${INST_NAME}.log"
}

stop_instance() {
    local yaml_path="$1"
    eval "$(parse_instance_yaml "$yaml_path")"

    local runtime_path="$INSTANCES_DIR/.runtime/${INST_NAME}.json"
    if [ ! -f "$runtime_path" ]; then
        echo "[SKIP]  vLLM $INST_NAME — 실행 중 아님 (runtime 없음)"
        return
    fi

    local pid
    pid=$(read_runtime_pid "$runtime_path")

    # PID 죽음 또는 launcher 아님(재사용) → 신호 보내지 않음. runtime 파일만 정리.
    # cmdline 검증으로 PID 재사용 케이스에서 다른 프로세스를 kill하는 사고 차단.
    if ! is_pid_alive "$pid" "vllm_server_launcher.py"; then
        echo "[CLEAN] vLLM $INST_NAME — runtime stale (PID ${pid:-?} launcher 아님/죽음), 정리만 수행"
        rm -f "$runtime_path"
        return
    fi

    echo "[STOP]  vLLM $INST_NAME (port :$INST_PORT, launcher PID $pid)"
    kill "$pid" 2>/dev/null || true

    # launcher의 finally 블록이 vLLM child terminate(최대 10s) + runtime 정리.
    # 폴링하여 정리 완료를 기다린다. 15초까지.
    local i
    for i in $(seq 1 15); do
        [ ! -f "$runtime_path" ] && break
        sleep 1
    done

    # 폴링 후에도 잔존 → launcher가 비정상 → SIGKILL + 직접 삭제.
    if [ -f "$runtime_path" ]; then
        if is_pid_alive "$pid" "vllm_server_launcher.py"; then
            echo "[KILL]  vLLM $INST_NAME — SIGTERM 무응답 15s, SIGKILL 강제 종료"
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$runtime_path"
        echo "        runtime 파일 직접 정리"
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
    if [ -z "$pid" ]; then
        echo "[SKIP]  Gateway $GW_NAME (:$GW_PORT) — 실행 중 아님"
        return
    fi

    echo "[STOP]  Gateway $GW_NAME (:$GW_PORT) PID $pid"
    kill "$pid" 2>/dev/null || true

    # 종료 폴링 — 직후 cmd_up이 [SKIP]로 잘못 판정하는 사고 방지.
    # uvicorn lifespan 종료(헬스체크 stop + httpx aclose)는 보통 1~2초.
    local i
    for i in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || return
        sleep 1
    done

    # 10초 무응답 → SIGKILL 강제 종료.
    echo "[KILL]  Gateway $GW_NAME — SIGTERM 무응답 10s, SIGKILL 강제 종료"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
}

cmd_up() {
    local target="${1:-}"
    local kind
    kind=$(detect_target_kind "$target") || exit 1

    case "$kind" in
        instance)
            echo "═══ 인스턴스 단독 기동: $target (게이트웨이 미터치) ═══"
            echo ""
            start_instance "$INSTANCES_DIR/${target}.yaml"
            echo ""
            # 기존 게이트웨이의 HealthChecker는 '이미 등록된' 인스턴스의 재기동만 자동 감지한다.
            # 게이트웨이가 모르는 새 인스턴스를 추가하는 경우엔 게이트웨이 재시작 필요
            # (HealthChecker._servers는 lifespan startup에서 한 번만 구성됨).
            echo "게이트웨이는 기존 상태 유지 (등록된 인스턴스 재기동은 HealthChecker가 자동 감지 / 신규 인스턴스 추가는 게이트웨이 restart 필요)"
            ;;
        gateway)
            echo "═══ 게이트웨이 단독 기동: $target (인스턴스 미터치) ═══"
            echo ""
            start_gateway "$GATEWAYS_DIR/${target}.yaml"
            ;;
        all)
            echo "═══ vLLM 클러스터 전체 시작 ═══"
            echo ""
            local started_runtimes=()
            while IFS= read -r yaml_path; do
                [ -z "$yaml_path" ] && continue
                start_instance "$yaml_path"
                # 방금 시작한 (또는 SKIP된) 인스턴스의 runtime 경로 수집 — 게이트웨이 기동 전 폴링용.
                local _name
                _name=$(basename "$yaml_path" .yaml)
                started_runtimes+=("$INSTANCES_DIR/.runtime/${_name}.json")
            done < <(list_instance_yamls)
            echo ""

            # 게이트웨이 디스커버리 시점에 launcher가 runtime json을 못 썼으면 yaml port hint로
            # fallback → launcher가 +1 자동 회피했다면 게이트웨이가 영영 unhealthy로 굳음.
            # runtime 등록 완료(또는 timeout)까지 대기.
            local pending=()
            local rt
            for rt in "${started_runtimes[@]}"; do
                [ -f "$rt" ] || pending+=("$rt")
            done
            if [ ${#pending[@]} -gt 0 ]; then
                echo "[WAIT]  launcher runtime 등록 대기 (최대 30s, 게이트웨이 디스커버리가 자동 회피 port를 잡도록)..."
                for rt in "${pending[@]}"; do
                    local _n
                    _n=$(basename "$rt" .json)
                    if wait_runtime_ready "$rt" 30; then
                        echo "        ✓ $_n"
                    else
                        echo "[WARN]  $_n — runtime 미등록(timeout 30s). 게이트웨이는 yaml port hint로 fallback. launcher가 +1 자동 회피했다면 영구 unhealthy — './start.sh status'로 확인 후 인스턴스/게이트웨이 restart 필요"
                    fi
                done
                echo ""
            fi

            while IFS= read -r yaml_path; do
                [ -z "$yaml_path" ] && continue
                start_gateway "$yaml_path"
            done < <(list_gateway_yamls)
            echo ""
            echo "상태 확인: ./start.sh status"
            ;;
    esac
}

cmd_down() {
    local target="${1:-}"
    local kind
    kind=$(detect_target_kind "$target") || exit 1

    case "$kind" in
        instance)
            echo "═══ 인스턴스 단독 중지: $target (게이트웨이 미터치) ═══"
            echo ""
            stop_instance "$INSTANCES_DIR/${target}.yaml"
            ;;
        gateway)
            echo "═══ 게이트웨이 단독 중지: $target (인스턴스 미터치) ═══"
            echo ""
            stop_gateway "$GATEWAYS_DIR/${target}.yaml"
            ;;
        all)
            echo "═══ vLLM 클러스터 전체 중지 ═══"
            echo ""
            # 게이트웨이 먼저 → 인스턴스 (트래픽 차단 후 모델 종료)
            while IFS= read -r yaml_path; do
                [ -z "$yaml_path" ] && continue
                stop_gateway "$yaml_path"
            done < <(list_gateway_yamls)
            echo ""
            while IFS= read -r yaml_path; do
                [ -z "$yaml_path" ] && continue
                stop_instance "$yaml_path"
            done < <(list_instance_yamls)
            ;;
    esac
    echo ""
    echo "완료"
}

cmd_status() {
    echo "═══ vLLM 클러스터 상태 ═══"

    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        eval "$(parse_instance_yaml "$yaml_path")"
        local runtime_path="$INSTANCES_DIR/.runtime/${INST_NAME}.json"

        if [ -f "$runtime_path" ]; then
            local pid
            pid=$(read_runtime_pid "$runtime_path")
            # PID 죽었거나 launcher 아님(재사용) → STALE. 신호 보내지 말고 정리 안내만.
            if ! is_pid_alive "$pid" "vllm_server_launcher.py"; then
                echo "[STALE]   vLLM $INST_NAME (port hint :$INST_PORT_HINT, runtime PID ${pid:-?} launcher 아님/죽음 — './start.sh down $INST_NAME'으로 정리)"
                continue
            fi
            local port_label=":$INST_PORT"
            if [ "$INST_PORT" != "$INST_PORT_HINT" ]; then
                port_label=":$INST_PORT (hint :$INST_PORT_HINT 자동 회피)"
            fi
            if is_running "$INST_PORT"; then
                echo "[UP]      vLLM $INST_NAME (GPU $INST_GPUS_CSV, $port_label, → gw :$INST_GATEWAY_PORT, PID $pid)"
            else
                echo "[STARTING] vLLM $INST_NAME (GPU $INST_GPUS_CSV, $port_label, PID $pid 살아있음 / health 응답 없음 — 모델 로딩 중)"
            fi
        else
            echo "[DOWN]    vLLM $INST_NAME (GPU $INST_GPUS_CSV, port hint :$INST_PORT_HINT, → gw :$INST_GATEWAY_PORT)"
        fi
    done < <(list_instance_yamls)

    echo ""
    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        eval "$(parse_gateway_yaml "$yaml_path")"

        # /health 본문에서 ready/total을 뽑아 백엔드 준비 상태와 게이트웨이 프로세스 생존을 분리.
        # 게이트웨이는 떠있어도 ready=0이면 503을 반환하므로, 단순 curl 성공/실패만으로는 모호.
        local body
        body=$(curl -s --connect-timeout 1 "http://127.0.0.1:$GW_PORT/health" 2>/dev/null || true)
        if [ -z "$body" ]; then
            echo "[DOWN]    Gateway $GW_NAME (:$GW_PORT)"
            continue
        fi
        local stats
        stats=$(python3 -c "import sys,json
try:
    d=json.loads(sys.stdin.read())
    print(f\"{d.get('ready','?')}/{d.get('total','?')}\")
except Exception:
    print('?/?')" <<<"$body" 2>/dev/null)
        local ready="${stats%%/*}"
        if [ "$ready" = "0" ]; then
            echo "[STARTING] Gateway $GW_NAME (:$GW_PORT, ready $stats — 백엔드 대기/웜업 중)"
        else
            echo "[UP]      Gateway $GW_NAME (:$GW_PORT, ready $stats)"
        fi
    done < <(list_gateway_yamls)
}

cmd_restart() {
    local target="${1:-}"
    # cmd_down 내부에서 stop_instance/stop_gateway가 종료 폴링까지 보장하므로 추가 sleep 불필요.
    cmd_down "$target"
    echo ""
    cmd_up "$target"
}

# source 시에는 함수만 정의되도록 main 가드. 직접 실행(./start.sh, bash start.sh)일 때만 case 분기 실행.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-up}" in
        up|start)     shift || true; cmd_up "${1:-}" ;;
        down|stop)    shift || true; cmd_down "${1:-}" ;;
        status)       cmd_status ;;
        restart)      shift || true; cmd_restart "${1:-}" ;;
        *)            echo "사용법: $0 {up|down|status|restart} [name]"; exit 1 ;;
    esac
fi
