#!/bin/bash
set -euo pipefail

# ============================================
# 다중 사용자 컨테이너 관리
#
# 사용법:
#   ./user.sh up <username> [--password <pw>] [--gpus <gpus>]
#   ./user.sh down <username>
#   ./user.sh list
#
# 포트 할당 (자동):
#   사용자 0: 5000(SSH), 5001-5009
#   사용자 1: 5010(SSH), 5011-5019
#   ...
#   최대 5490(SSH) 까지 (인바운드 5000-5500)
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 5000-5009는 docker-compose 메인 서비스용 (예약)
PORT_BASE=5010
PORT_STEP=10
PORT_MAX=5500
CONTAINER_PREFIX="llm-"
IMAGE_NAME="llm-dev"

# .env 로드 (VOLUME_PATH, HF_TOKEN 등)
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

VOLUME_PATH="${VOLUME_PATH:-/volume}"
HF_TOKEN="${HF_TOKEN:-}"
EXTRA_REQUIREMENTS="${EXTRA_REQUIREMENTS:-}"
SHM_SIZE="${SHM_SIZE:-16g}"
LLM_MEMORY="${LLM_MEMORY:-360g}"

usage() {
    echo "사용법:"
    echo "  $0 up <username> [--password <pw>] [--gpus <gpus>]"
    echo "  $0 down <username>"
    echo "  $0 list"
    exit 1
}

# 사용 중인 포트 베이스 목록 조회
get_used_bases() {
    docker ps -a --filter "name=^${CONTAINER_PREFIX}" --format '{{.Names}}' 2>/dev/null | while read -r name; do
        # 컨테이너의 SSH 포트(호스트 바인딩)에서 베이스 추출
        ssh_port=$(docker port "$name" 5555 2>/dev/null | head -1 | cut -d: -f2 || true)
        if [ -n "$ssh_port" ]; then
            echo "$ssh_port"
        fi
    done
}

# 다음 사용 가능한 포트 베이스 찾기
next_port_base() {
    local used_bases
    used_bases=$(get_used_bases | sort -n)

    local base=$PORT_BASE
    while [ $base -lt $PORT_MAX ]; do
        if ! echo "$used_bases" | grep -q "^${base}$"; then
            echo "$base"
            return
        fi
        base=$((base + PORT_STEP))
    done

    echo ""
}

# 컨테이너가 이미 존재하는지 확인
container_exists() {
    docker ps -a --filter "name=^${CONTAINER_PREFIX}${1}$" --format '{{.Names}}' 2>/dev/null | grep -q .
}

cmd_up() {
    local username=""
    local password="changeme"
    local gpus="all"

    # 인자 파싱
    while [ $# -gt 0 ]; do
        case "$1" in
            --password) password="$2"; shift 2 ;;
            --gpus) gpus="$2"; shift 2 ;;
            -*) echo "알 수 없는 옵션: $1"; usage ;;
            *) username="$1"; shift ;;
        esac
    done

    if [ -z "$username" ]; then
        echo "❌ username을 지정해주세요."
        usage
    fi

    # 이미 존재하는 컨테이너 확인
    if container_exists "$username"; then
        local state
        state=$(docker inspect -f '{{.State.Status}}' "${CONTAINER_PREFIX}${username}" 2>/dev/null || true)
        if [ "$state" = "running" ]; then
            echo "✅ ${CONTAINER_PREFIX}${username} 이미 실행 중"
            docker port "${CONTAINER_PREFIX}${username}"
            return
        else
            echo "🔄 중지된 컨테이너 재시작: ${CONTAINER_PREFIX}${username}"
            docker start "${CONTAINER_PREFIX}${username}"
            echo "✅ 재시작 완료"
            docker port "${CONTAINER_PREFIX}${username}"
            return
        fi
    fi

    # 이미지 확인
    if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
        echo "⚠️ 이미지 '$IMAGE_NAME'이 없습니다. 먼저 빌드하세요:"
        echo "   cd $SCRIPT_DIR && docker compose build"
        exit 1
    fi

    # 포트 할당
    local base
    base=$(next_port_base)
    if [ -z "$base" ]; then
        echo "❌ 포트 범위 초과 (최대 ${PORT_MAX}). 더 이상 사용자를 생성할 수 없습니다."
        exit 1
    fi

    local ssh_port=$base
    local extra_start=$((base + 1))
    local extra_end=$((base + PORT_STEP - 1))

    # 디렉토리 생성
    mkdir -p "${VOLUME_PATH}/workspace/${username}"
    mkdir -p "${VOLUME_PATH}/homes/${username}"

    # GPU 옵션
    local gpu_opts=""
    if [ "$gpus" = "all" ]; then
        gpu_opts="--gpus all"
    else
        gpu_opts="--gpus '\"device=${gpus}\"'"
    fi

    echo "🚀 컨테이너 생성: ${CONTAINER_PREFIX}${username}"
    echo "   SSH: ssh -p ${ssh_port} ${username}@<host>"
    echo "   포트: ${extra_start}-${extra_end} (1:1 매핑)"
    echo "   GPU: ${gpus}"

    # docker run
    eval docker run -d \
        --name "${CONTAINER_PREFIX}${username}" \
        --hostname "llm-${username}" \
        --restart unless-stopped \
        --security-opt apparmor=unconfined \
        --security-opt seccomp=unconfined \
        --shm-size "$SHM_SIZE" \
        --memory "$LLM_MEMORY" \
        -p "${ssh_port}:5555" \
        -p "${extra_start}-${extra_end}:${extra_start}-${extra_end}" \
        -v "${VOLUME_PATH}/workspace/${username}:/workspace" \
        -v "${VOLUME_PATH}/models:/models" \
        -v "${VOLUME_PATH}/data:/data" \
        -v "${VOLUME_PATH}/homes/${username}:/home/${username}" \
        -e "USERNAME=${username}" \
        -e "PASSWORD=${password}" \
        -e "CONTAINER_UID=1000" \
        -e "CONTAINER_GID=1000" \
        -e "HF_TOKEN=${HF_TOKEN}" \
        -e "EXTRA_REQUIREMENTS=${EXTRA_REQUIREMENTS}" \
        -e "NVIDIA_VISIBLE_DEVICES=${gpus}" \
        ${gpu_opts} \
        "$IMAGE_NAME"

    echo "✅ 완료"
}

cmd_down() {
    local username="$1"
    if [ -z "$username" ]; then
        echo "❌ username을 지정해주세요."
        usage
    fi

    if ! container_exists "$username"; then
        echo "❌ 컨테이너 '${CONTAINER_PREFIX}${username}'이 없습니다."
        exit 1
    fi

    echo "🗑️ 컨테이너 중지 및 제거: ${CONTAINER_PREFIX}${username}"
    docker stop "${CONTAINER_PREFIX}${username}" 2>/dev/null || true
    docker rm "${CONTAINER_PREFIX}${username}" 2>/dev/null || true
    echo "✅ 완료 (데이터는 ${VOLUME_PATH}에 보존됨)"
}

cmd_list() {
    echo "=== LLM 사용자 컨테이너 ==="
    local found=false
    docker ps -a --filter "name=^${CONTAINER_PREFIX}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | while IFS= read -r line; do
        found=true
        echo "$line"
    done
    if [ "$found" = false ]; then
        echo "(없음)"
    fi
}

# ============================================
# 메인
# ============================================
[ $# -lt 1 ] && usage

CMD="$1"
shift

case "$CMD" in
    up)   cmd_up "$@" ;;
    down) cmd_down "${1:-}" ;;
    list) cmd_list ;;
    *)    usage ;;
esac
