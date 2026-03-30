#!/bin/bash
set -euo pipefail

# ============================================
# EC2 GPU 서버 초기 세팅 스크립트
# 대상: Amazon Linux 2023 + NVIDIA GPU (L40S, H200 등)
#
# 사용법:
#   chmod +x setup-ec2.sh
#   sudo ./setup-ec2.sh
#
# Phase 1: 시스템 + Docker + NVIDIA 드라이버 → 자동 reboot
# Phase 2: Container Toolkit + Compose + Fabric Manager → 완료
# ============================================

SCRIPT_PATH=$(realpath "$0")
PHASE_FILE="/var/tmp/ec2-setup-phase"
LOG_FILE="/var/log/ec2-setup.log"

# 기본 설정 (.env 파일 또는 환경변수로 오버라이드 가능)
USERNAME="${USERNAME:-}"
PASSWORD="${PASSWORD:-}"
VOLUME_PATH="${VOLUME_PATH:-/volume}"
SSH_PORT="${SSH_PORT:-5555}"
CONTAINER_UID="${CONTAINER_UID:-1000}"
CONTAINER_GID="${CONTAINER_GID:-1000}"

# EBS 볼륨 디바이스 경로 (lsblk로 확인 후 설정)
VOLUME_DEVICE="${VOLUME_DEVICE:-}"         # 예: /dev/nvme1n1
CUDA_TEST_IMAGE="${CUDA_TEST_IMAGE:-nvidia/cuda:12.8.1-base-ubuntu24.04}"

# ============================================
# 유틸리티 함수
# ============================================
log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" | tee -a "$LOG_FILE"
}

error_exit() {
    log "❌ 에러: $1"
    exit 1
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error_exit "root 권한이 필요합니다. sudo ./setup-ec2.sh 으로 실행하세요."
    fi
}

# EBS 볼륨을 포맷 + 마운트 + fstab 등록
mount_ebs_volume() {
    local device="$1"
    local mount_point="$2"

    if [ -z "$device" ]; then
        log "  ⚠️ ${mount_point}: VOLUME_DEVICE 미설정. 로컬 디렉토리로 대체합니다."
        log "     데이터가 루트 디스크에 저장되므로, EBS 사용 시 .env의 VOLUME_DEVICE를 설정하세요."
        mkdir -p "$mount_point"
        return
    fi

    if [ ! -b "$device" ]; then
        log "  ⚠️ ${device} 블록 디바이스가 존재하지 않습니다. lsblk 확인 필요."
        mkdir -p "$mount_point"
        return
    fi

    mkdir -p "$mount_point"

    # 이미 마운트되어 있으면 건너뜀
    if mountpoint -q "$mount_point" 2>/dev/null; then
        log "  ${mount_point}: 이미 마운트됨. 건너뜀."
        return
    fi

    # 파일시스템 확인 → 없으면 xfs로 포맷
    local fs_type
    fs_type=$(blkid -o value -s TYPE "$device" 2>/dev/null || true)
    if [ -z "$fs_type" ]; then
        log "  ${device} → xfs 포맷 중..."
        mkfs -t xfs -f "$device"
    else
        log "  ${device}: 기존 파일시스템 ${fs_type} 감지. 포맷 건너뜀."
    fi

    # 마운트
    mount "$device" "$mount_point"
    log "  ${device} → ${mount_point} 마운트 완료"

    # fstab 등록 (중복 방지)
    local uuid
    uuid=$(blkid -o value -s UUID "$device" 2>/dev/null || true)
    if [ -z "$uuid" ]; then
        log "  ⚠️ ${device}의 UUID를 읽을 수 없습니다. fstab 등록 건너뜀."
        return
    fi
    if ! grep -q "$uuid" /etc/fstab; then
        cp /etc/fstab /etc/fstab.bak
        local fstab_type
        fstab_type=$(blkid -o value -s TYPE "$device" 2>/dev/null || echo "xfs")
        echo "UUID=${uuid} ${mount_point} ${fstab_type} defaults,nofail 0 2" >> /etc/fstab
        log "  fstab 등록 완료 (UUID=${uuid})"
    else
        log "  fstab에 이미 등록됨. 건너뜀."
    fi
}

# ============================================
# Phase 1: 시스템 + Docker + NVIDIA 드라이버
# ============================================
phase1() {
    log "========== Phase 1 시작 =========="

    # --- 사용자 생성 ---
    if [ -n "$USERNAME" ]; then
        log "[1/9] 사용자 생성: $USERNAME"
        if id "$USERNAME" &>/dev/null; then
            log "  사용자 $USERNAME 이미 존재. 건너뜀."
        else
            # UID/GID 충돌 확인 (ec2-user 등이 CONTAINER_UID/GID를 점유할 수 있음)
            # 주의: 초기 세팅 전용. 운영 중인 서버에서는 기존 사용자 파일 소유권이 변경될 수 있음
            local existing_uid_user
            existing_uid_user=$(getent passwd "$CONTAINER_UID" | cut -d: -f1 || true)
            if [ -n "$existing_uid_user" ]; then
                local new_uid=$(( CONTAINER_UID + 5000 ))
                log "  UID ${CONTAINER_UID}를 ${existing_uid_user}이(가) 사용 중 → UID ${new_uid}로 변경"
                usermod -u "$new_uid" "$existing_uid_user"
            fi
            local existing_gid_group
            existing_gid_group=$(getent group "$CONTAINER_GID" | cut -d: -f1 || true)
            if [ -n "$existing_gid_group" ]; then
                local new_gid=$(( CONTAINER_GID + 5000 ))
                log "  GID ${CONTAINER_GID}를 ${existing_gid_group}이(가) 사용 중 → GID ${new_gid}로 변경"
                groupmod -g "$new_gid" "$existing_gid_group"
            fi
            groupadd -g "$CONTAINER_GID" "$USERNAME" 2>/dev/null || true
            useradd -m -s /bin/bash -u "$CONTAINER_UID" -g "$CONTAINER_GID" "$USERNAME"
            if [ -n "$PASSWORD" ]; then
                echo "${USERNAME}:${PASSWORD}" | chpasswd
            fi
            # sudoers.d 방식 (안전)
            echo "${USERNAME} ALL=(ALL:ALL) ALL" > "/etc/sudoers.d/${USERNAME}"
            chmod 0440 "/etc/sudoers.d/${USERNAME}"
            log "  사용자 생성 완료 + sudo 권한 부여"
        fi
    else
        log "[1/9] USERNAME 미설정. 사용자 생성 건너뜀."
    fi

    # --- SSH 설정 + fail2ban ---
    log "[2/9] SSH 설정 + fail2ban (포트: ${SSH_PORT})"
    sed -i "s/^#\?Port .*/Port ${SSH_PORT}/" /etc/ssh/sshd_config
    grep -q "^Port ${SSH_PORT}" /etc/ssh/sshd_config || echo "Port ${SSH_PORT}" >> /etc/ssh/sshd_config
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
    grep -q "^PasswordAuthentication yes" /etc/ssh/sshd_config || echo "PasswordAuthentication yes" >> /etc/ssh/sshd_config
    sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config
    sed -i 's/^#\?MaxAuthTries.*/MaxAuthTries 5/' /etc/ssh/sshd_config
    grep -q "^MaxAuthTries" /etc/ssh/sshd_config || echo "MaxAuthTries 5" >> /etc/ssh/sshd_config
    # cloud-init이 SSH 설정을 덮어쓰지 않도록 드롭인 파일 정리
    rm -f /etc/ssh/sshd_config.d/50-cloud-init.conf 2>/dev/null || true
    systemctl restart sshd

    # fail2ban 설치 + SSH jail 설정
    if dnf install -y fail2ban 2>/dev/null; then
        cat > /etc/fail2ban/jail.local <<JAIL
[sshd]
enabled = true
port = ${SSH_PORT}
maxretry = 5
bantime = 3600
findtime = 600
JAIL
        systemctl enable fail2ban
        systemctl start fail2ban
        log "  fail2ban 활성화 완료"
    else
        log "  ⚠️ fail2ban 설치 실패. AL2023.6 이상에서 dnf install -y fail2ban 으로 수동 설치하세요."
    fi
    log "  SSH 포트 ${SSH_PORT}, 비밀번호 인증 활성화 완료"

    # --- EBS 볼륨 마운트 ---
    log "[3/9] EBS 볼륨 마운트"
    if [ -n "$VOLUME_DEVICE" ]; then
        log "  사용 가능한 블록 디바이스:"
        lsblk -o NAME,SIZE,TYPE,MOUNTPOINT | tee -a "$LOG_FILE"
    fi
    mount_ebs_volume "$VOLUME_DEVICE" "$VOLUME_PATH"

    # --- 작업/데이터 디렉토리 설정 ---
    log "[4/9] 작업/데이터 디렉토리 설정"
    chmod 775 "$VOLUME_PATH"
    mkdir -p "${VOLUME_PATH}/workspace"
    mkdir -p "${VOLUME_PATH}/data"
    mkdir -p "${VOLUME_PATH}/models"
    mkdir -p "${VOLUME_PATH}/homes"
    if [ -n "$USERNAME" ]; then
        chown "$USERNAME":"$USERNAME" "$VOLUME_PATH"
        mkdir -p "${VOLUME_PATH}/workspace/${USERNAME}"
        mkdir -p "${VOLUME_PATH}/homes/${USERNAME}"
        chown -R "$USERNAME":"$USERNAME" "${VOLUME_PATH}/workspace/${USERNAME}"
        chown -R "$USERNAME":"$USERNAME" "${VOLUME_PATH}/homes/${USERNAME}"
        log "  ${VOLUME_PATH}/{workspace,homes}/${USERNAME} 생성 완료"
    fi

    # --- 시스템 업데이트 + 커널 패키지 ---
    log "[5/9] 시스템 업데이트 + 커널 패키지 설치"
    dnf update -y --exclude='kernel*'
    dnf install -y \
        dnf-plugins-core curl wget git jq htop tmux \
        gcc dkms --allowerasing

    # AL2023 커널 패키지명 자동 감지 (6.1: kernel-devel, 6.12+: kernel6.12-devel)
    local kver kpkg
    kver=$(uname -r)
    case "$kver" in
        6.1.*) kpkg="kernel" ;;
        *)     kpkg="kernel$(echo "$kver" | cut -d. -f1,2)" ;;
    esac
    log "  커널 패키지 접두사: ${kpkg} (커널: ${kver})"
    dnf install -y "${kpkg}-devel-${kver}" "${kpkg}-headers-${kver}" --allowerasing
    dnf install -y "${kpkg}-modules-extra-${kver}" 2>/dev/null || true
    dnf install -y "${kpkg}-modules-extra-common-${kver}" 2>/dev/null || true

    # --- Docker 설치 ---
    log "[6/9] Docker 설치"
    if command -v docker &>/dev/null; then
        log "  Docker 이미 설치됨. 건너뜀."
    else
        dnf install -y docker
        systemctl start docker
        systemctl enable docker
    fi
    # 기본 사용자들을 docker 그룹에 추가
    for u in ec2-user ssm-user ${USERNAME:-}; do
        if id "$u" &>/dev/null; then
            usermod -aG docker "$u" 2>/dev/null || true
        fi
    done
    log "  Docker 설치 완료"

    # --- Docker Compose V2 + Buildx 플러그인 ---
    log "[7/9] Docker Compose V2 + Buildx 설치"
    mkdir -p /usr/libexec/docker/cli-plugins
    if docker compose version &>/dev/null; then
        log "  Docker Compose 이미 설치됨. 건너뜀."
    else
        curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
            -o /usr/libexec/docker/cli-plugins/docker-compose
        chmod +x /usr/libexec/docker/cli-plugins/docker-compose
        log "  Docker Compose $(docker compose version --short) 설치 완료"
    fi
    if docker buildx version &>/dev/null; then
        log "  Docker Buildx 이미 설치됨. 건너뜀."
    else
        local buildx_arch="amd64"
        [ "$(uname -m)" = "aarch64" ] && buildx_arch="arm64"
        curl -fsSL "https://github.com/docker/buildx/releases/download/v0.21.2/buildx-v0.21.2.linux-${buildx_arch}" \
            -o /usr/libexec/docker/cli-plugins/docker-buildx
        chmod +x /usr/libexec/docker/cli-plugins/docker-buildx
        log "  Docker Buildx $(docker buildx version 2>/dev/null | head -1 || echo 'installed') 설치 완��"
    fi

    # --- Claude Code ---
    log "[8/9] Claude Code 설치"
    if [ -n "$USERNAME" ]; then
        if su - "$USERNAME" -c "command -v claude" &>/dev/null; then
            log "  Claude Code 이미 설치됨. 건너뜀."
        else
            if su - "$USERNAME" -c "curl -fsSL https://claude.ai/install.sh | bash" 2>/dev/null; then
                log "  Claude Code 설치 완료"
            else
                log "  ⚠️ Claude Code 설치 실패. 수동 설치: curl -fsSL https://claude.ai/install.sh | bash"
            fi
        fi
    else
        log "  USERNAME 미설정. 건너뜀."
    fi

    # --- NVIDIA 드라이버 ---
    log "[9/9] NVIDIA 드라이버 설치"
    if nvidia-smi &>/dev/null; then
        log "  NVIDIA 드라이버 이미 설치됨. 건너뜀."
        echo "2" > "$PHASE_FILE"
        log "========== Phase 1 완료 (reboot 불필요) =========="
        phase2
        return
    fi

    # NVIDIA repo 추가 + 드라이버 설치
    dnf config-manager --add-repo \
        https://developer.download.nvidia.com/compute/cuda/repos/amzn2023/x86_64/cuda-amzn2023.repo 2>/dev/null || true
    dnf clean expire-cache
    # CUDA repo의 모듈 스트림 활성화 후 오픈소스 드라이버 설치
    dnf module enable -y nvidia-driver:open-dkms 2>/dev/null || true
    dnf install -y nvidia-open

    # Phase 2 자동 실행을 위한 systemd 서비스 등록
    echo "2" > "$PHASE_FILE"
    register_phase2_service

    log "========== Phase 1 완료. 10초 후 reboot =========="
    log "  reboot 후 Phase 2가 자동 실행됩니다."
    log "  진행 상황: tail -f $LOG_FILE"
    sleep 10
    reboot
}

# ============================================
# Phase 2: Container Toolkit + Fabric Manager
# ============================================
phase2() {
    # Phase 2 실패 시 systemd 서비스 + phase 파일 정리 (리부트 루프 방지)
    trap 'cleanup_phase2_service; rm -f "$PHASE_FILE"' ERR

    log "========== Phase 2 시작 =========="

    # --- NVIDIA 드라이버 확인 ---
    log "[1/4] NVIDIA 드라이버 확인"
    if nvidia-smi &>/dev/null; then
        nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | while read -r line; do
            log "  GPU: $line"
        done
    else
        error_exit "NVIDIA 드라이버가 로드되지 않았습니다. dmesg | grep -i nvidia 확인 필요"
    fi

    # --- NVIDIA Container Toolkit ---
    log "[2/4] NVIDIA Container Toolkit 설치"
    if command -v nvidia-ctk &>/dev/null; then
        log "  Container Toolkit 이미 설치됨. 건너뜀."
    else
        curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | \
            tee /etc/yum.repos.d/nvidia-container-toolkit.repo
        dnf clean expire-cache
        dnf install -y nvidia-container-toolkit
        nvidia-ctk runtime configure --runtime=docker
        systemctl restart docker
        log "  Container Toolkit 설치 완료"
    fi

    # --- Fabric Manager (NVSwitch GPU 자동 감지) ---
    log "[3/4] Fabric Manager 확인"
    local needs_fm=false
    if nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -qiE "H100|H200|A100|B100|B200"; then
        needs_fm=true
    fi

    if [ "$needs_fm" = true ]; then
        log "  NVSwitch GPU 감지 → Fabric Manager 설치"
        local driver_version
        driver_version=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | tr -d ' ')

        # CUDA repo 추가 (Fabric Manager 패키지용 — Phase 1과 동일한 amzn2023 repo 사용)
        dnf config-manager --add-repo \
            https://developer.download.nvidia.com/compute/cuda/repos/amzn2023/x86_64/cuda-amzn2023.repo 2>/dev/null || true
        dnf clean expire-cache

        # AL2023는 모듈 프로파일(/fm) 방식이 공식 가이드
        # 현재 드라이버 브랜치에 맞는 open 스트림을 우선 시도 → 실패 시 open-dkms 폴백
        local driver_branch
        driver_branch="${driver_version%%.*}-open"
        if dnf module install -y "nvidia-driver:${driver_branch}/fm" --allowerasing 2>/dev/null \
            || dnf module install -y "nvidia-driver:open-dkms/fm" --allowerasing 2>/dev/null; then
            systemctl enable nvidia-fabricmanager
            systemctl start nvidia-fabricmanager
            log "  Fabric Manager 설치 + 시작 완료 (driver=${driver_version})"
        else
            log "  ⚠️ Fabric Manager 자동 설치 실패. 수동 설치 필요:"
            log "     dnf module install -y nvidia-driver:${driver_branch}/fm --allowerasing"
        fi
    else
        log "  NVSwitch GPU 미감지 (L40S 등). Fabric Manager 불필요."
    fi

    # --- Docker GPU 테스트 ---
    log "[4/4] Docker GPU 연동 테스트"
    if docker run --rm --gpus all "$CUDA_TEST_IMAGE" nvidia-smi &>/dev/null; then
        log "  ✅ Docker GPU 연동 정상"
    else
        log "  ⚠️ Docker GPU 테스트 실패. docker 재시작 후 재시도 필요"
    fi

    # Phase 2 완료 — ERR trap 해제 후 정리
    trap - ERR
    cleanup_phase2_service
    rm -f "$PHASE_FILE"

    log ""
    log "============================================"
    log "  ✅ EC2 GPU 서버 세팅 완료"
    log "============================================"
    log ""
    log "  NVIDIA Driver : $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
    log "  Docker        : $(docker --version)"
    log "  Compose       : $(docker compose version --short 2>/dev/null || echo 'N/A')"
    log "  GPU           :"
    nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | while read -r line; do
        log "    $line"
    done
    log ""
    log "  다음 단계:"
    log "    cd $(dirname "$SCRIPT_PATH")"
    log "    cp .env.example .env  # 설정 수정"
    log "    docker compose up -d llm"
    log ""
    log "  로그: $LOG_FILE"
    log "============================================"
}

# ============================================
# systemd 서비스: reboot 후 phase2 자동 실행
# ============================================
register_phase2_service() {
    cat > /etc/systemd/system/ec2-setup-phase2.service <<UNIT
[Unit]
Description=EC2 GPU Setup Phase 2
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=/bin/bash "${SCRIPT_PATH}" --phase2
RemainAfterExit=no
StandardOutput=journal+console

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable ec2-setup-phase2.service
    log "  Phase 2 자동 실행 서비스 등록 완료"
}

cleanup_phase2_service() {
    if [ -f /etc/systemd/system/ec2-setup-phase2.service ]; then
        systemctl disable ec2-setup-phase2.service 2>/dev/null || true
        rm -f /etc/systemd/system/ec2-setup-phase2.service
        systemctl daemon-reload
        log "  Phase 2 서비스 정리 완료"
    fi
}

# ============================================
# 메인 실행
# ============================================
main() {
    check_root

    # .env 파일이 있으면 항상 로드 (phase2 자동 실행 시에도 적용)
    if [ -f "$(dirname "$SCRIPT_PATH")/.env" ]; then
        log ".env 파일 로드"
        # Windows 줄 끝(CRLF) 제거
        sed -i 's/\r$//' "$(dirname "$SCRIPT_PATH")/.env"
        set -a
        source "$(dirname "$SCRIPT_PATH")/.env"
        set +a
    fi

    case "${1:-}" in
        --phase2)
            phase2
            ;;
        --phase1)
            phase1
            ;;
        *)
            # 자동 판별: phase 파일이 있으면 phase2, 없으면 phase1
            if [ -f "$PHASE_FILE" ] && [ "$(cat "$PHASE_FILE")" = "2" ]; then
                phase2
            else
                phase1
            fi
            ;;
    esac
}

main "$@"
