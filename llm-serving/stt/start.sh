#!/bin/bash
# ═══════════════════════════════════════════════════════
# STT 클러스터 시작/중지/상태 스크립트 (vLLM 기반)
#
# vllm/start.sh 와 동일한 instances/+gateways/ 페어 구조를 따른다.
# launcher / 게이트웨이 파이썬 코드는 ../vllm/ 하위의 것을 재사용한다 (코드 단일 출처).
#
# 디렉토리 규약:
#   instances/<name>.yaml   — STT vLLM 인스턴스 1대 정의 (port, gpus, model, gateway_port, task)
#   gateways/<port>.yaml    — STT 게이트웨이 1대 정의 (gateway.port, discover_from)
#
# 게이트웨이는 instances/*.yaml 중 gateway_port == 자기 포트인 것을
# 자동으로 backends에 등록한다(vllm_gateway.py의 discover_from).
# STT 게이트웨이는 vllm_gateway 본체를 재사용하므로 chat/completions 외에도
# /v1/audio/transcriptions(POST) 와 /v1/realtime(WebSocket) 라우트를 제공한다.
#
# 사용법 ([name]은 인스턴스/게이트웨이 yaml 파일명에서 자동 감지):
#   ./start.sh up                # 인자 없음 → 전체 적용 confirm 프롬프트 [y/N]
#   ./start.sh up all            # 전체 인스턴스 + 모든 게이트웨이 기동 (확인 없이)
#   ./start.sh up voxtral        # instances/voxtral.yaml 단독 기동 (게이트웨이 미터치)
#   ./start.sh up 5017           # gateways/5017.yaml 단독 기동 (인스턴스 미터치)
#   ./start.sh down              # 인자 없음 → 전체 중지 confirm 프롬프트 [y/N]
#   ./start.sh down all          # 모든 인스턴스 + 게이트웨이 중지 (확인 없이)
#   ./start.sh down voxtral      # instances/voxtral.yaml 단독 중지
#   ./start.sh down 5017         # gateways/5017.yaml 단독 중지
#   ./start.sh status            # 상태 확인
#   ./start.sh restart           # 인자 없음 → 전체 재시작 confirm 프롬프트 [y/N]
#   ./start.sh restart <name>    # 단일 대상 재시작 (내부적으로 down→up)
#
# 안전 정책: 무인자 호출은 [y/N] 기본 No로 묻는다 (사고 방지).
# 비대화 환경(파이프/cron)에서는 'all' 또는 이름 명시 필수 (prompt 띄울 곳이 없으므로 거부).
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
    if [ -z "$target" ] || [ "$target" = "all" ]; then
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

# 무인자 호출 시 사용자에게 [y/N]로 전체 적용 여부를 묻고 RESOLVED_TARGET을 결정.
# - target 명시(이름/포트/'all') → 그대로 통과 (prompt 없음)
# - 무인자 + tty → confirm 프롬프트, y면 RESOLVED_TARGET="all", 아니면 즉시 종료
# - 무인자 + non-tty(파이프/cron) → prompt 띄울 곳이 없으므로 사고 방지 차원에서 exit 1
#
# subshell 캡처(target=$(...))를 쓰면 helper 안 exit가 caller까지 종료시키지 못해
# "사용자가 N으로 거부했는데 caller가 빈 target으로 'all' 처리하는" 사고가 난다.
# → 전역 변수에 결과를 적고, helper가 직접 exit 0/1로 전체 스크립트 흐름을 끊는다.
RESOLVED_TARGET=""
resolve_target_or_confirm() {
    local target="${1:-}"
    local action="$2"
    if [ -n "$target" ]; then
        RESOLVED_TARGET="$target"
        return 0
    fi
    if [ ! -t 0 ]; then
        echo "ERROR: 대상 미지정. 비대화 환경에서는 './start.sh $action all' 또는 이름 명시 필요." >&2
        exit 1
    fi
    local ans=""
    read -r -p "전체 인스턴스+게이트웨이를 '$action' 하시겠습니까? [y/N]: " ans
    if [[ ! "$ans" =~ ^[Yy]$ ]]; then
        echo "취소됨." >&2
        exit 0
    fi
    RESOLVED_TARGET="all"
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
    nohup python "$SCRIPT_DIR/../vllm/vllm_server_launcher.py" \
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
    nohup python "$SCRIPT_DIR/../vllm/vllm_gateway.py" -c "$yaml_path" \
        > "$LOG_DIR/gateway_${GW_NAME}.log" 2>&1 &
    echo "        PID $!, 로그: logs/gateway_${GW_NAME}.log"
}

stop_gateway() {
    local yaml_path="$1"
    eval "$(parse_gateway_yaml "$yaml_path")"

    # set -e 환경에서 caller(cmd_down)가 비제로 반환에 의해 조기 종료되지 않도록
    # 함수 내 모든 return은 명시적으로 return 0 (line 297의 함정 사고 학습).
    if [ -z "$GW_PORT" ]; then
        return 0
    fi

    local pid
    pid=$(get_pid "$GW_PORT")
    if [ -z "$pid" ]; then
        echo "[SKIP]  Gateway $GW_NAME (:$GW_PORT) — 실행 중 아님"
        return 0
    fi

    echo "[STOP]  Gateway $GW_NAME (:$GW_PORT) PID $pid"
    kill "$pid" 2>/dev/null || true

    # 종료 폴링 — 직후 cmd_up이 [SKIP]로 잘못 판정하는 사고 방지.
    # uvicorn lifespan 종료(헬스체크 stop + httpx aclose)는 보통 1~2초.
    #
    # 주의: `return`은 인자 없으면 직전 명령(kill -0)의 종료코드를 그대로 반환한다.
    # 프로세스가 죽었으면 kill -0이 1을 반환 → return 1 → set -e 환경에서 caller(cmd_down)가
    # 비제로 반환을 만나 즉시 종료되어 다음 단계(인스턴스 stop loop)가 건너뛰어졌다.
    # 정상 종료는 명시적으로 `return 0`.
    local i
    for i in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 1
    done

    # 10초 무응답 → SIGKILL 강제 종료.
    echo "[KILL]  Gateway $GW_NAME — SIGTERM 무응답 10s, SIGKILL 강제 종료"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
}

cmd_up() {
    resolve_target_or_confirm "${1:-}" "up"
    local target="$RESOLVED_TARGET"
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
            echo "═══ STT 클러스터 전체 시작 ═══"
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
    # 무인자 → confirm 프롬프트로 전체 중지 의사 확인. 'all' 또는 이름 명시는 prompt 없이 진행.
    # (다른 모델/게이트웨이 운영에 영향이 가지 않도록 무인자는 안전쪽 [y/N] 기본 No.)
    resolve_target_or_confirm "${1:-}" "down"
    local target="$RESOLVED_TARGET"
    local kind
    kind=$(detect_target_kind "$target") || exit 1

    case "$kind" in
        all)
            echo "═══ STT 클러스터 전체 중지 ═══"
            echo ""
            # 게이트웨이 먼저 정리해 신규 요청 유입을 차단한 뒤 인스턴스를 종료한다.
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
    esac
    echo ""
    echo "완료"
}

cmd_status() {
    echo "═══ STT 클러스터 상태 ═══"

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
    # 여기서 prompt를 한 번만 띄우고, 결정된 target('all' 또는 이름)을 cmd_down/cmd_up에 명시 전달.
    # 그러면 두 함수의 resolve_target_or_confirm은 인자 명시 분기로 빠져 prompt를 다시 띄우지 않는다.
    resolve_target_or_confirm "${1:-}" "restart"
    local target="$RESOLVED_TARGET"
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
        *)            echo "사용법: $0 {up [name|all] | down|stop [name|all] | restart [name|all] | status}"; exit 1 ;;
    esac
fi
