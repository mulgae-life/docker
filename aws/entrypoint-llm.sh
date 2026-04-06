#!/bin/bash
set -euo pipefail

# ============================================
# 런타임 사용자 생성
# ============================================
USERNAME="${USERNAME:-user}"
PASSWORD="${PASSWORD:-changeme}"
CONTAINER_UID="${CONTAINER_UID:-1000}"
CONTAINER_GID="${CONTAINER_GID:-1000}"

# 홈 디렉토리 기본 설정
setup_user_home() {
    local home_dir="$1"
    su - "$USERNAME" -c "git config --global --add safe.directory /workspace && git config --global core.quotePath false" || true
    echo "set -g mouse on" > "$home_dir/.tmux.conf"
    # CUDA + pip 사용자 패키지 PATH (중복 추가 방지)
    if ! grep -q '/usr/local/cuda/bin' "$home_dir/.bashrc" 2>/dev/null; then
        echo 'export PATH="/usr/local/cuda/bin:$HOME/.local/bin:$PATH"' >> "$home_dir/.bashrc"
        echo 'export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' >> "$home_dir/.bashrc"
    fi
    # nvm 설정 (sudo 없이 npm 사용)
    if ! grep -q 'NVM_DIR' "$home_dir/.bashrc" 2>/dev/null; then
        echo 'export NVM_DIR=/usr/local/nvm' >> "$home_dir/.bashrc"
        echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"' >> "$home_dir/.bashrc"
    fi
    chown -R "${USERNAME}:${USERNAME}" "$home_dir"
}

if ! id "$USERNAME" &>/dev/null; then
    # 기존 UID/GID 충돌 제거
    existing_user=$(getent passwd "$CONTAINER_UID" | cut -d: -f1 || true)
    [ -n "$existing_user" ] && userdel -r "$existing_user" 2>/dev/null || true
    existing_group=$(getent group "$CONTAINER_GID" | cut -d: -f1 || true)
    [ -n "$existing_group" ] && groupdel "$existing_group" 2>/dev/null || true

    groupadd -g "$CONTAINER_GID" "$USERNAME" 2>/dev/null || true
    useradd -m -s /bin/bash -u "$CONTAINER_UID" -g "$CONTAINER_GID" "$USERNAME"
    echo "${USERNAME}:${PASSWORD}" | chpasswd
    usermod -aG sudo "$USERNAME"

    setup_user_home "/home/${USERNAME}"
fi

# ============================================
# 홈 디렉토리 초기화 (bind mount 시 빈 디렉토리 복원)
# ============================================
HOME_DIR="/home/$USERNAME"
if [ -d "$HOME_DIR" ] && [ -z "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
    # 빈 bind mount -> 기본 파일 복사
    cp -a /etc/skel/. "$HOME_DIR/" 2>/dev/null || true
    setup_user_home "$HOME_DIR"
elif [ -d "$HOME_DIR" ] && [ "$(stat -c %u "$HOME_DIR")" != "$CONTAINER_UID" ]; then
    chown -R "${USERNAME}:${USERNAME}" "$HOME_DIR"
fi

# ============================================
# 런타임 requirements 설치
# ============================================
if [ -n "${EXTRA_REQUIREMENTS:-}" ]; then
    if [ -f "${EXTRA_REQUIREMENTS}" ]; then
        echo "==> 추가 패키지 설치: $EXTRA_REQUIREMENTS"
        pip install --no-cache-dir --break-system-packages -q -r "$EXTRA_REQUIREMENTS"
    else
        echo "⚠️ EXTRA_REQUIREMENTS=${EXTRA_REQUIREMENTS} 파일이 존재하지 않습니다." >&2
    fi
fi

# nvm 소유권 (사용자가 npm global 패키지 설치 가능하도록)
chown -R "${USERNAME}:${USERNAME}" /usr/local/nvm

# ============================================
# Docker 환경변수를 SSH 세션에서도 사용할 수 있도록
# (대화형 + 비대화형 SSH 모두 적용)
# ※ Claude Code 설치 체크보다 먼저 생성해야 su - 에서 PATH 참조 가능
# ============================================
{
    echo 'export NVM_DIR=/usr/local/nvm'
    echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'
    echo 'export PATH="/usr/local/cuda/bin:$HOME/.local/bin:$PATH"'
    echo 'export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"'
    [ -n "${HF_TOKEN:-}" ] && echo "export HF_TOKEN=\"$HF_TOKEN\""
    [ -n "${ASSIGNED_GPUS:-}" ] && echo "export NVIDIA_VISIBLE_DEVICES=\"$ASSIGNED_GPUS\""
    true
} > /etc/profile.d/docker-env.sh

# ============================================
# Claude Code 설치 (사용자별, 홈 bind mount 시 persist)
# ============================================
if [ ! -f "/home/${USERNAME}/.local/bin/claude" ]; then
    echo "==> Claude Code 설치: ${USERNAME}"
    su - "$USERNAME" -c "curl -fsSL https://claude.ai/install.sh | bash" || echo "⚠️ Claude Code 설치 실패 (네트워크 문제일 수 있음)" >&2
fi

exec "$@"
