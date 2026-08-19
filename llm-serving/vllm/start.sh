#!/bin/bash
# ═══════════════════════════════════════════════════════
# vLLM 클러스터 시작/중지/상태/모델동기화 스크립트
#
# 디렉토리 규약:
#   instances/<name>.yaml   — vLLM 인스턴스 1대 정의 (port, gpus, model, gateway_port)
#   gateways/<port>.yaml    — 게이트웨이 1대 정의 (gateway.port, discover_from)
#
# 게이트웨이는 instances/*.yaml 중 gateway_port == 자기 포트인 것을
# 자동으로 backends에 등록한다(vllm_gateway.py의 discover_from).
#
# 사용법 ([name]은 인스턴스/게이트웨이 yaml 파일명에서 자동 감지):
#   ./start.sh up                # 인자 없음 → 전체 적용 confirm 프롬프트 [y/N]
#   ./start.sh up all            # 전체 인스턴스 + 모든 게이트웨이 기동 (확인 없이)
#   ./start.sh up gemma          # instances/gemma.yaml 단독 기동 (게이트웨이 미터치)
#   ./start.sh up 5015           # gateways/5015.yaml 단독 기동 (인스턴스 미터치)
#   ./start.sh down              # 인자 없음 → 전체 중지 confirm 프롬프트 [y/N]
#   ./start.sh down all          # 모든 인스턴스 + 게이트웨이 중지 (확인 없이)
#   ./start.sh down qwen         # instances/qwen.yaml 단독 중지
#   ./start.sh down 5016         # gateways/5016.yaml 단독 중지
#   ./start.sh status            # 상태 확인
#   ./start.sh restart           # 인자 없음 → 전체 재시작 confirm 프롬프트 [y/N]
#   ./start.sh restart <name>    # 단일 대상 재시작 (내부적으로 down→up)
#   ./start.sh logs              # 전체 인스턴스+게이트웨이 로그 tail -F (기본 -n 50)
#   ./start.sh logs <name>       # 단일 대상 tail -F (instances/<name>.yaml 또는 gateways/<name>.yaml)
#   ./start.sh logs --lines N    # 초기 라인 수 오버라이드 (예: --lines 200)
#   ./start.sh download <name>   # 모델 다운로드/최신 동기화 (서빙 미터치, 변경 파일만 증분)
#   ./start.sh download all      # 전체 인스턴스 모델 동기화 — 폐쇄망: 네트워크 개방 시점에 실행
#   ./start.sh test              # 기동된 게이트웨이 전부에 기능 QA (미기동 대상은 SKIP)
#   ./start.sh test 5015         # gateways/5015.yaml 단독
#   ./start.sh test gemma        # instances/gemma.yaml의 실제 포트에 직접 (게이트웨이 미경유)
#   ./start.sh test <대상> --category infra   # 대상 뒤 인자는 tests/의 해당 스크립트로 그대로 전달
#   ./start.sh test http://host:5015          # 원격 서버 대상
#   ./start.sh speed <대상>      # 속도 매트릭스 측정 (tests/results/speed_results.md에 누적)
#   ./start.sh traffic 5015      # 하드 부하 테스트(텍스트+이미지 반반) — 게이트웨이만, 대상 명시 필수
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
# 본체는 vllm/. wrapper(stt/start.sh 등)에서 INSTANCES_DIR/GATEWAYS_DIR/LOG_DIR/CLUSTER_LABEL을
# export하여 클러스터별 디렉토리/라벨로 동작시킨다. 미설정 시 vllm/ 자체로 동작.
# 단, launcher/gateway 파이썬 본체는 항상 vllm/ 기준($SCRIPT_DIR)에서 호출한다.
INSTANCES_DIR="${INSTANCES_DIR:-$SCRIPT_DIR/instances}"
GATEWAYS_DIR="${GATEWAYS_DIR:-$SCRIPT_DIR/gateways}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
CLUSTER_LABEL="${CLUSTER_LABEL:-vLLM}"
# test/speed/traffic 세 명령이 위임하는 스위트. 클러스터마다 검증 항목이 다르므로
# (vLLM은 chat/tool/멀티모달, STT는 transcription) wrapper가 자기 스위트를 export한다.
# ':-'가 아니라 '-'를 쓴다 — wrapper가 빈 문자열을 export해 "이 클러스터는 미지원"을
# 표시할 수 있어야 한다(기본값으로 되돌아가면 STT에 vLLM용 스위트가 날아간다).
TEST_SCRIPT="${TEST_SCRIPT-$SCRIPT_DIR/tests/test_vllm_server.py}"
SPEED_SCRIPT="${SPEED_SCRIPT-$SCRIPT_DIR/tests/speed_test.py}"
TRAFFIC_SCRIPT="${TRAFFIC_SCRIPT-$SCRIPT_DIR/tests/traffic_test_vllm.py}"
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
    # awk의 `~`는 substring/regex 매칭이라 port=":5015"가 ":50152"도 prefix 매칭한다.
    # 끝 앵커($)를 붙여 "끝이 :<port>인 행"만 잡는다. 안 그러면 같은 prefix를 가진
    # 다른 포트의 무관 프로세스(예: VSCode Pylance가 잡은 :50152)에 SIGTERM이 가서
    # 다중 PID 매칭으로 stop_gateway의 종료 폴링이 꼬인다.
    netstat -tlnp 2>/dev/null | awk -v port=":$1\$" '$4 ~ port {split($7,a,"/"); print a[1]}' || true
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

# 기존 로그를 타임스탬프 파일로 보존한 뒤 자리를 비운다. 기동이 `> logs/<name>.log`로
# 덮어쓰므로, 이 호출이 없으면 크래시 직후 재기동 시 원인 로그를 잃는다.
# 백업 파일은 자동 삭제하지 않는다 — 정리는 운영자 판단.
rotate_log() {
    local log_path="$1"
    [ -s "$log_path" ] || return 0
    local backup="${log_path%.log}.$(date +%Y%m%d_%H%M%S).log"
    mv "$log_path" "$backup" 2>/dev/null || return 0
    echo "        이전 로그 보존: $(basename "$backup")"
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
    rotate_log "$LOG_DIR/vllm_${INST_NAME}.log"
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
    rotate_log "$LOG_DIR/gateway_${GW_NAME}.log"
    nohup python "$SCRIPT_DIR/vllm_gateway.py" -c "$yaml_path" \
        > "$LOG_DIR/gateway_${GW_NAME}.log" 2>&1 &
    echo "        PID $!, 로그: logs/gateway_${GW_NAME}.log"
}

stop_gateway() {
    local yaml_path="$1"
    eval "$(parse_gateway_yaml "$yaml_path")"

    # set -e 환경에서 caller(cmd_down)가 비제로 반환에 의해 조기 종료되지 않도록
    # 함수 내 모든 return은 명시적으로 return 0 (line 292의 함정 사고 학습).
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
            echo "═══ $CLUSTER_LABEL 클러스터 전체 시작 ═══"
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
            echo "═══ $CLUSTER_LABEL 클러스터 전체 중지 ═══"
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
    echo "═══ $CLUSTER_LABEL 클러스터 상태 ═══"

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

cmd_logs() {
    # 인자 파싱 — 이름/all 1개 + --lines N(또는 -n N) 옵션. 순서 무관.
    # default 결정 근거:
    #   - 무인자 → 'all' (read-only 명령이라 confirm 불필요, 전체 흐름 관찰이 주 용도)
    #   - -n 50 (vLLM 부팅 로그가 길어 10은 부족, 100+는 다중 tail 시 첫 화면 압도)
    local target="" lines="50"
    while [ $# -gt 0 ]; do
        case "$1" in
            --lines|-n) lines="${2:-}"; shift 2 ;;
            --lines=*)  lines="${1#*=}"; shift ;;
            -n=*)       lines="${1#*=}"; shift ;;
            *)          [ -z "$target" ] && target="$1" || { echo "ERROR: 인자 과다: $1" >&2; exit 1; }; shift ;;
        esac
    done
    [ -z "$target" ] && target="all"
    [[ "$lines" =~ ^[0-9]+$ ]] || { echo "ERROR: --lines 값은 정수: '$lines'" >&2; exit 1; }

    local kind
    kind=$(detect_target_kind "$target") || exit 1

    case "$kind" in
        instance)
            local f="$LOG_DIR/vllm_${target}.log"
            [ -f "$f" ] || { echo "ERROR: $f 없음 (아직 기동되지 않음 — './start.sh up $target' 후 재시도)" >&2; exit 1; }
            exec tail -n "$lines" -F "$f"
            ;;
        gateway)
            local f="$LOG_DIR/gateway_${target}.log"
            [ -f "$f" ] || { echo "ERROR: $f 없음 (아직 기동되지 않음 — './start.sh up $target' 후 재시도)" >&2; exit 1; }
            exec tail -n "$lines" -F "$f"
            ;;
        all)
            # 모든 yaml 정의 대상에 대해 로그 파일 경로 수집.
            # tail -F는 존재하지 않는 파일도 polling으로 대기 → 일부 미기동 대상이 섞여도 안전.
            local files=()
            while IFS= read -r p; do
                [ -z "$p" ] && continue
                local n; n=$(basename "$p" .yaml)
                files+=("$LOG_DIR/vllm_${n}.log")
            done < <(list_instance_yamls)
            while IFS= read -r p; do
                [ -z "$p" ] && continue
                local n; n=$(basename "$p" .yaml)
                files+=("$LOG_DIR/gateway_${n}.log")
            done < <(list_gateway_yamls)
            [ ${#files[@]} -eq 0 ] && { echo "ERROR: tail 대상 없음 (instances/, gateways/에 yaml 없음)" >&2; exit 1; }
            exec tail -n "$lines" -F "${files[@]}"
            ;;
    esac
}

cmd_download() {
    # 모델 다운로드/최신 동기화 — 서빙 프로세스 미터치. 대상은 인스턴스만(게이트웨이는 모델 없음).
    # 로컬 모델이 이미 있으면 launcher(--download-only)가 HF 최신 리비전과 증분 동기화
    # (변경 파일만 다운로드 — 가중치 무변경 시 chat_template 등 소형 파일만 받음).
    # 폐쇄망 절차: 네트워크 개방 → download → 차단 → up (up은 네트워크 미접근).
    local target="${1:-}"
    if [ -z "$target" ]; then
        if [ ! -t 0 ]; then
            echo "ERROR: 대상 미지정. 비대화 환경에서는 './start.sh download all' 또는 이름 명시 필요." >&2
            exit 1
        fi
        local ans=""
        read -r -p "전체 인스턴스 모델을 다운로드/동기화 하시겠습니까? [y/N]: " ans
        if [[ ! "$ans" =~ ^[Yy]$ ]]; then
            echo "취소됨." >&2
            exit 0
        fi
        target="all"
    fi

    local kind
    kind=$(detect_target_kind "$target") || exit 1
    if [ "$kind" = "gateway" ]; then
        echo "ERROR: '$target'은 게이트웨이 — 모델이 없어 download 대상이 아닙니다. 인스턴스 이름을 지정하세요." >&2
        exit 1
    fi

    local yamls=()
    if [ "$kind" = "instance" ]; then
        yamls=("$INSTANCES_DIR/${target}.yaml")
    else
        while IFS= read -r p; do
            [ -n "$p" ] && yamls+=("$p")
        done < <(list_instance_yamls)
    fi
    [ ${#yamls[@]} -eq 0 ] && { echo "ERROR: 대상 없음 (instances/에 yaml 없음)" >&2; exit 1; }

    echo "═══ $CLUSTER_LABEL 모델 다운로드/동기화 (${#yamls[@]}개 인스턴스, 서빙 미터치) ═══"
    local fail=0 yaml_path name
    for yaml_path in "${yamls[@]}"; do
        name=$(basename "$yaml_path" .yaml)
        echo ""
        echo "── $name ──"
        # foreground 실행 — 다운로드 진행/네트워크 에러를 터미널에서 그대로 확인.
        if ! python "$SCRIPT_DIR/vllm_server_launcher.py" -c "$yaml_path" --download-only; then
            echo "[FAIL]  $name — 다운로드 실패 (네트워크 개방 여부/HF_TOKEN 확인)" >&2
            fail=1
        fi
    done
    echo ""
    if [ "$fail" -ne 0 ]; then
        echo "일부 대상 실패 — 위 로그 확인 후 재시도: ./start.sh download <name>" >&2
        exit 1
    fi
    echo "완료. 실행 중인 서빙에 반영하려면 재기동 필요: ./start.sh restart <name>"
}

# [name] → 테스트 대상 base URL. 인스턴스는 runtime의 실제 port(자동 회피 반영),
# 게이트웨이는 gateway.port를 쓴다. 매칭 실패 시 stderr 안내 후 return 1.
resolve_test_url() {
    local target="$1" kind
    kind=$(detect_target_kind "$target") || return 1
    case "$kind" in
        instance)
            eval "$(parse_instance_yaml "$INSTANCES_DIR/${target}.yaml")"
            if [ -z "$INST_PORT" ]; then
                echo "ERROR: $target — port를 알 수 없습니다 (yaml에 port 없음, runtime 미등록)" >&2
                return 1
            fi
            echo "http://localhost:$INST_PORT"
            ;;
        gateway)
            eval "$(parse_gateway_yaml "$GATEWAYS_DIR/${target}.yaml")"
            if [ -z "$GW_PORT" ]; then
                echo "ERROR: $target — gateway.port 없음" >&2
                return 1
            fi
            echo "http://localhost:$GW_PORT"
            ;;
        *)
            echo "ERROR: '$target' — 단일 대상이 아닙니다" >&2
            return 1
            ;;
    esac
}

# test/speed/traffic 세 명령의 공통 실행기. 판정·측정 로직은 tests/의 각 스위트가
# 갖고 있고, 여기서는 [name] → base URL 변환과 다중 대상 순회만 담당한다(제어 스크립트 SRP).
#   $1 스크립트 경로  $2 화면 표시용 라벨  $3 무인자/all 허용(1/0)  $4 게이트웨이 전용(1/0)
#   나머지는 스위트 스크립트로 넘길 사용자 인자
run_suite() {
    local script="$1" label="$2" allow_all="$3" gateway_only="$4"
    shift 4

    # 인자 규칙: 첫 자리에 '-'로 시작하지 않는 인자가 오면 그것이 대상, 나머지는 전부
    # 스위트 스크립트로 그대로 전달. '비대시 인자를 훑어서 대상으로 잡는' 방식을 쓰면
    # --category가 nargs="*"라 './start.sh test --category infra'의 infra를 대상으로
    # 오인한다 (cmd_logs의 --lines는 값이 하나뿐이라 그 방식이 통했지만 여기선 깨진다).
    local target=""
    if [ $# -gt 0 ] && [[ "$1" != -* ]]; then
        target="$1"
        shift
    fi

    # 빈 문자열 = 이 클러스터가 지원하지 않는 스위트. wrapper가 명시적으로 비워 표시한다
    # (기본값으로 두면 STT에 vLLM용 chat completions 스위트가 날아가므로 빈 값으로 막는다).
    if [ -z "$script" ]; then
        echo "ERROR: $CLUSTER_LABEL 클러스터는 '$label' 명령을 지원하지 않습니다." >&2
        exit 1
    fi
    if [ ! -f "$script" ]; then
        echo "ERROR: 테스트 스크립트 없음: $script" >&2
        echo "  wrapper 클러스터라면 start.sh의 export 경로를 확인하세요." >&2
        exit 1
    fi

    local a
    for a in "$@"; do
        # --list처럼 서버가 필요 없는 조회 플래그는 대상 해석 없이 그대로 위임.
        [ "$a" = "--list" ] && exec python "$script" "$@"
        # base URL은 이 함수가 [name]을 변환해 넘긴다. 직접 주면 인자가 두 번 붙고,
        # 대상 미지정 시엔 게이트웨이 순회마다 같은 곳을 반복 호출하게 된다.
        if [ "$a" = "--base-url" ] || [[ "$a" == --base-url=* ]]; then
            echo "ERROR: --base-url은 여기서 직접 지정할 수 없습니다. 'http://host:port'를 대상 자리에 쓰세요." >&2
            exit 1
        fi
    done

    # 부하 스위트는 대상을 반드시 찍게 한다. 무인자 순회를 허용하면 운영 게이트웨이까지
    # 동시 부하를 받아 실사용자 응답이 느려진다.
    if [ "$allow_all" -eq 0 ] && { [ -z "$target" ] || [ "$target" = "all" ]; }; then
        echo "ERROR: $label — 대상을 명시해야 합니다 (전체 순회 불가)." >&2
        echo "  예) ./start.sh traffic $(list_gateway_yamls | head -1 | xargs -n1 basename 2>/dev/null | sed 's/\.yaml$//')" >&2
        exit 1
    fi

    # 게이트웨이 전용 스위트에 인스턴스를 넘기는 것을 막는다. traffic은 통과 판정에
    # /server-status를 넣는데 이 엔드포인트는 vllm_gateway.py에만 있고 launcher에는 없다.
    # 인스턴스를 겨냥하면 404가 떠서 부하 결과와 무관하게 항상 "통과: False"가 된다.
    # (이 검사는 위의 미지원/미존재 검사보다 뒤에 와야 한다 — 스위트 자체가 없는 클러스터에서
    #  "게이트웨이를 쓰세요"라고 안내하면 없는 기능으로 사용자를 두 번 걷게 만든다.)
    if [ "$gateway_only" -eq 1 ] && [ -n "$target" ] && [[ "$target" != http* ]] \
       && [ -f "$INSTANCES_DIR/${target}.yaml" ]; then
        echo "ERROR: '$target' — 인스턴스입니다. $label는 게이트웨이만 대상으로 합니다." >&2
        echo "  통과 조건에 게이트웨이 전용 /server-status가 들어가 인스턴스는 항상 404로 실패합니다." >&2
        echo "  게이트웨이: $(list_gateway_yamls | xargs -n1 basename 2>/dev/null | sed 's/\.yaml$//' | tr '\n' ' ')" >&2
        exit 1
    fi

    # 원격 대상 — yaml에 없는 서버(운영계 등)를 직접 겨냥할 때.
    if [[ "$target" == http://* || "$target" == https://* ]]; then
        echo "═══ $CLUSTER_LABEL $label: $target (원격 지정) ═══"
        exec python "$script" --base-url "$target" "$@"
    fi

    # 단일 대상은 기동 여부를 여기서 판정하지 않는다 — 미기동/무응답은 스위트 스크립트의
    # 연결 확인 단계가 원인과 함께 안내하고 exit 1 한다. 이름을 찍어 지정했다는 것은
    # "그 대상이 떠 있어야 한다"는 기대이므로 SKIP으로 삼키면 안 된다.
    if [ -n "$target" ] && [ "$target" != "all" ]; then
        local url
        url=$(resolve_test_url "$target") || exit 1
        echo "═══ $CLUSTER_LABEL $label: $target ($url) ═══"
        exec python "$script" --base-url "$url" "$@"
    fi

    # all — 게이트웨이만 순회한다. 인스턴스는 게이트웨이 뒤에 있어 같은 경로를 두 번 때린다.
    # 게이트웨이 6대 중 일부만 띄우는 것이 정상 운영 형태라, 미기동 대상은 FAIL이 아니라
    # SKIP으로 넘긴다(안 띄운 대상의 실패로 실제 실패가 묻히는 것을 막는다).
    echo "═══ $CLUSTER_LABEL 클러스터 전체 $label (기동된 게이트웨이 대상) ═══"
    local ran=0 skipped=0 failed_names=()
    while IFS= read -r yaml_path; do
        [ -z "$yaml_path" ] && continue
        eval "$(parse_gateway_yaml "$yaml_path")"
        [ -z "$GW_PORT" ] && continue
        if ! is_running "$GW_PORT"; then
            echo "[SKIP]  Gateway $GW_NAME (:$GW_PORT) — 실행 중 아님"
            skipped=$((skipped + 1))
            continue
        fi
        ran=$((ran + 1))
        echo ""
        echo "── Gateway $GW_NAME (:$GW_PORT) ──"
        if ! python "$script" --base-url "http://localhost:$GW_PORT" "$@"; then
            failed_names+=("$GW_NAME")
        fi
    done < <(list_gateway_yamls)

    echo ""
    if [ "$ran" -eq 0 ]; then
        echo "ERROR: 기동된 게이트웨이가 없습니다 (SKIP $skipped개) — './start.sh status'로 확인 후 './start.sh up' 필요" >&2
        exit 1
    fi
    echo "═══ $label 요약: 실행 $ran / 실패 ${#failed_names[@]} / 건너뜀 $skipped ═══"
    if [ ${#failed_names[@]} -gt 0 ]; then
        echo "실패 대상: ${failed_names[*]} — 각 대상 로그는 tests/logs/ 참고" >&2
        exit 1
    fi
}

#                                              라벨          all  gw전용
cmd_test()    { run_suite "$TEST_SCRIPT"    "QA 테스트"    1    0 "$@"; }
cmd_speed()   { run_suite "$SPEED_SCRIPT"   "속도 측정"    1    0 "$@"; }
cmd_traffic() { run_suite "$TRAFFIC_SCRIPT" "부하 테스트"  0    1 "$@"; }

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

cmd_help() {
    # 등록된 인스턴스/게이트웨이 이름을 동적으로 읽어 표시 (wrapper인 STT에서도 자기 목록으로 나옴).
    local inst gw
    inst=$(list_instance_yamls | xargs -n1 basename 2>/dev/null | sed 's/\.yaml$//' | tr '\n' ' ')
    gw=$(list_gateway_yamls | xargs -n1 basename 2>/dev/null | sed 's/\.yaml$//' | tr '\n' ' ')
    local first_inst="${inst%% *}" first_gw="${gw%% *}"   # 예시용 첫 항목 (파이프 없이 첫 단어)

    # speed/traffic은 클러스터마다 지원 여부가 다르다(STT는 해당 테스트 스크립트가 없어 미지원).
    # 도움말에 그대로 두면 없는 기능을 광고하게 되므로, 스크립트가 비어 있으면 줄 자체를 뺀다.
    # 각 변수는 개행으로 끝나고 heredoc에서 붙여 쓰므로, 비면 빈 줄도 남지 않는다.
    local speed_cmd="" traffic_cmd="" speed_ex="" traffic_ex="" traffic_policy=""
    if [ -n "$SPEED_SCRIPT" ]; then
        speed_cmd="  speed [name|all|URL] 속도 측정 — TTFT/TPS 매트릭스, 결과는 tests/results/에 누적
"
        speed_ex="  ./start.sh speed ${first_gw:-<포트>} --quick   # 속도 1조합만 (연결 확인용)
"
    fi
    if [ -n "$TRAFFIC_SCRIPT" ]; then
        traffic_cmd="  traffic <포트|URL>   하드 부하 테스트 (동시 20, 절반은 이미지) — 게이트웨이만, 대상 명시 필수
"
        traffic_ex="  ./start.sh traffic ${first_gw:-<포트>} --concurrency 50   # 부하 강도 지정
  ./start.sh traffic ${first_gw:-<포트>} --image-ratio 0     # 텍스트만 (기본은 절반이 이미지)
"
        traffic_policy="           traffic은 부하가 크므로 무인자/all 호출을 아예 거부한다.
"
    fi

    cat <<EOF
$CLUSTER_LABEL 클러스터 제어 스크립트

사용법: ./start.sh <명령> [name|all] [옵션]

명령:
  up [name|all]        기동 (= start). 무인자는 [y/N]로 전체 적용 확인
  down [name|all]      정지 (= stop). 무인자는 [y/N]로 전체 적용 확인
  restart [name|all]   재기동 (down→up)
  status               전체 상태 (인스턴스 + 게이트웨이)
  logs [name|all]      로그 tail -F (기본 -n 50, --lines N 으로 오버라이드)
  download [name|all]  모델 다운로드/최신 동기화 (서빙 미터치, 네트워크 필요)
  test [name|all|URL]  기능 QA (무인자는 기동된 게이트웨이 전부, 미기동은 SKIP)
                       대상 뒤 인자는 tests/의 해당 스크립트로 그대로 전달
${speed_cmd}${traffic_cmd}  help                 이 도움말

[name] 자리:
  all        전체 인스턴스 + 게이트웨이
  <이름>     instances/<이름>.yaml 단독 (현재: ${inst:-없음})
  <포트>     gateways/<포트>.yaml 단독 (현재: ${gw:-없음})

예시:
  ./start.sh up all                # 전체 기동
  ./start.sh up ${first_inst:-<이름>}   # 인스턴스 단독 (게이트웨이 미터치)
  ./start.sh up ${first_gw:-<포트>}   # 게이트웨이 단독 (인스턴스 미터치)
  ./start.sh logs --lines 200      # 초기 200줄부터 전체 로그 추적
  ./start.sh status                # 상태 확인
  ./start.sh download ${first_inst:-<이름>}   # 모델 최신 동기화 (변경 파일만 증분)
  ./start.sh test                  # 기동된 게이트웨이 전부 검증
  ./start.sh test ${first_gw:-<포트>}          # 게이트웨이 1대만
  ./start.sh test ${first_inst:-<이름>} --category infra inference   # 인스턴스 직접, 카테고리 한정
  ./start.sh test --list           # 테스트 카테고리 목록
${speed_ex}${traffic_ex}
안전 정책: 무인자 up/down/restart/download는 [y/N] 기본 No (사고 방지).
           비대화 환경(파이프/cron)은 'all' 또는 이름 명시 필수.
${traffic_policy}폐쇄망 절차: 네트워크 개방 → download → 차단 → up (up은 네트워크 미접근).
EOF
}

# source 시에는 함수만 정의되도록 main 가드. 직접 실행(./start.sh, bash start.sh)일 때만 case 분기 실행.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-help}" in
        up|start)       shift || true; cmd_up "${1:-}" ;;
        down|stop)      shift || true; cmd_down "${1:-}" ;;
        status)         cmd_status ;;
        restart)        shift || true; cmd_restart "${1:-}" ;;
        logs)           shift || true; cmd_logs "$@" ;;
        download)       shift || true; cmd_download "${1:-}" ;;
        test)           shift || true; cmd_test "$@" ;;
        speed)          shift || true; cmd_speed "$@" ;;
        traffic)        shift || true; cmd_traffic "$@" ;;
        help|-h|--help) cmd_help ;;
        *)              echo "ERROR: 알 수 없는 명령 '$1'" >&2; echo "" >&2; cmd_help >&2; exit 1 ;;
    esac
fi
