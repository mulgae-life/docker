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
SETUP_USERNAME="${SETUP_USERNAME:-}"
SETUP_PASSWORD="${SETUP_PASSWORD:-}"
DATA_PATH="${DATA_PATH:-/data}"

# EBS 볼륨 디바이스 경로 (lsblk로 확인 후 설정)
WORKSPACE_DEVICE="${WORKSPACE_DEVICE:-}"   # 예: /dev/nvme1n1
DATA_DEVICE="${DATA_DEVICE:-}"             # 예: /dev/nvme2n1

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
        log "  ${mount_point}: 디바이스 미설정. 건너뜀."
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
    uuid=$(blkid -o value -s UUID "$device")
    if ! grep -q "$uuid" /etc/fstab; then
        cp /etc/fstab /etc/fstab.bak
        echo "UUID=${uuid} ${mount_point} xfs defaults,nofail 0 2" >> /etc/fstab
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
    if [ -n "$SETUP_USERNAME" ]; then
        log "[1/8] 사용자 생성: $SETUP_USERNAME"
        if id "$SETUP_USERNAME" &>/dev/null; then
            log "  사용자 $SETUP_USERNAME 이미 존재. 건너뜀."
        else
            useradd -m -s /bin/bash "$SETUP_USERNAME"
            if [ -n "$SETUP_PASSWORD" ]; then
                echo "${SETUP_USERNAME}:${SETUP_PASSWORD}" | chpasswd
            fi
            # sudoers.d 방식 (안전)
            echo "${SETUP_USERNAME} ALL=(ALL:ALL) ALL" > "/etc/sudoers.d/${SETUP_USERNAME}"
            chmod 0440 "/etc/sudoers.d/${SETUP_USERNAME}"
            log "  사용자 생성 완료 + sudo 권한 부여"
        fi
    else
        log "[1/8] SETUP_USERNAME 미설정. 사용자 생성 건너뜀."
    fi

    # --- SSH 설정 ---
    log "[2/8] SSH 설정"
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
    sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config
    systemctl restart sshd
    log "  SSH 비밀번호 인증 활성화 완료"

    # --- EBS 볼륨 마운트 ---
    log "[3/8] EBS 볼륨 마운트"
    if [ -n "$WORKSPACE_DEVICE" ] || [ -n "$DATA_DEVICE" ]; then
        log "  사용 가능한 블록 디바이스:"
        lsblk -o NAME,SIZE,TYPE,MOUNTPOINT | tee -a "$LOG_FILE"
    fi
    mount_ebs_volume "$WORKSPACE_DEVICE" "/workspace"
    mount_ebs_volume "$DATA_DEVICE" "$DATA_PATH"

    # --- 작업/데이터 디렉토리 권한 ---
    log "[4/8] 작업/데이터 디렉토리 설정"
    chmod 775 "$DATA_PATH"
    chmod 775 /workspace
    if [ -n "$SETUP_USERNAME" ]; then
        chown "$SETUP_USERNAME":"$SETUP_USERNAME" "$DATA_PATH"
        # 유저별 workspace: /workspace/<username> → 컨테이너 내 /workspace로 마운트
        mkdir -p "/workspace/${SETUP_USERNAME}"
        chown "$SETUP_USERNAME":"$SETUP_USERNAME" "/workspace/${SETUP_USERNAME}"
        log "  /workspace/${SETUP_USERNAME} 생성 완료"
    fi

    # --- 시스템 업데이트 + 커널 패키지 ---
    log "[5/8] 시스템 업데이트 + 커널 패키지 설치"
    dnf update -y
    dnf install -y \
        dnf-plugins-core curl wget git jq htop tmux \
        kernel-devel-"$(uname -r)" kernel-headers-"$(uname -r)" \
        gcc dkms --allowerasing
    dnf install -y \
        kernel-modules-extra-"$(uname -r)" \
        kernel-modules-extra-common-"$(uname -r)" 2>/dev/null || true

    # --- Docker 설치 ---
    log "[6/8] Docker 설치"
    if command -v docker &>/dev/null; then
        log "  Docker 이미 설치됨. 건너뜀."
    else
        dnf install -y docker
        systemctl start docker
        systemctl enable docker
    fi
    # 기본 사용자들을 docker 그룹에 추가
    for u in ec2-user ssm-user ${SETUP_USERNAME:-}; do
        if id "$u" &>/dev/null; then
            usermod -aG docker "$u" 2>/dev/null || true
        fi
    done
    log "  Docker 설치 완료"

    # --- Docker Compose V2 플러그인 ---
    log "[7/8] Docker Compose V2 설치"
    if docker compose version &>/dev/null; then
        log "  Docker Compose 이미 설치됨. 건너뜀."
    else
        mkdir -p /usr/libexec/docker/cli-plugins
        curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
            -o /usr/libexec/docker/cli-plugins/docker-compose
        chmod +x /usr/libexec/docker/cli-plugins/docker-compose
        log "  Docker Compose $(docker compose version --short) 설치 완료"
    fi

    # --- NVIDIA 드라이버 ---
    log "[8/8] NVIDIA 드라이버 설치"
    if nvidia-smi &>/dev/null; then
        log "  NVIDIA 드라이버 이미 설치됨. 건너뜀."
        echo "2" > "$PHASE_FILE"
        log "========== Phase 1 완료 (reboot 불필요) =========="
        phase2
        return
    fi

    # NVIDIA repo 추가 + 드라이버 설치
    dnf install -y nvidia-release 2>/dev/null || true
    dnf config-manager --add-repo \
        https://developer.download.nvidia.com/compute/cuda/repos/amzn2023/x86_64/cuda-amzn2023.repo 2>/dev/null || true
    dnf clean expire-cache
    dnf module reset -y nvidia-driver 2>/dev/null || true
    dnf module enable -y nvidia-driver:open-dkms
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

        # CUDA repo 추가 (Fabric Manager 패키지용)
        dnf config-manager --add-repo \
            https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64/cuda-rhel9.repo 2>/dev/null || true
        dnf clean expire-cache

        # 드라이버 버전에 맞는 Fabric Manager 설치 시도
        if dnf install -y "nvidia-fabric-manager-${driver_version}" 2>/dev/null; then
            systemctl enable nvidia-fabricmanager
            systemctl start nvidia-fabricmanager
            log "  Fabric Manager ${driver_version} 설치 + 시작 완료"
        else
            log "  ⚠️ Fabric Manager 자동 설치 실패. 수동 설치 필요:"
            log "     dnf install -y nvidia-fabric-manager-<버전>"
        fi
    else
        log "  NVSwitch GPU 미감지 (L40S 등). Fabric Manager 불필요."
    fi

    # --- Docker GPU 테스트 ---
    log "[4/4] Docker GPU 연동 테스트"
    if docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi &>/dev/null; then
        log "  ✅ Docker GPU 연동 정상"
    else
        log "  ⚠️ Docker GPU 테스트 실패. docker 재시작 후 재시도 필요"
    fi

    # Phase 2 완료 정리
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
    log "    cd /path/to/aws"
    log "    cp .env.example .env  # 설정 수정"
    log "    docker compose up -d llm-serve"
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
ExecStart=/bin/bash ${SCRIPT_PATH} --phase2
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
