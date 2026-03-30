#!/bin/bash
set -euo pipefail

# ============================================
# 런타임 사용자 생성
# ============================================
USERNAME="${USERNAME:-user}"
PASSWORD="${PASSWORD:-changeme}"
CONTAINER_UID="${CONTAINER_UID:-1000}"
CONTAINER_GID="${CONTAINER_GID:-1000}"

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

    # 기본 설정
    su - "$USERNAME" -c "git config --global --add safe.directory /workspace && git config --global core.quotePath false"
    echo "set -g mouse on" > "/home/${USERNAME}/.tmux.conf"
    chown "${USERNAME}:${USERNAME}" "/home/${USERNAME}/.tmux.conf"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "/home/${USERNAME}/.bashrc"
fi

# ============================================
# 홈 디렉토리 초기화 (bind mount 시 빈 디렉토리 복원)
# ============================================
HOME_DIR="/home/$USERNAME"
if [ -d "$HOME_DIR" ] && [ -z "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
    # 빈 bind mount → 기본 파일 복사
    cp -a /etc/skel/. "$HOME_DIR/" 2>/dev/null || true
    su - "$USERNAME" -c "git config --global --add safe.directory /workspace && git config --global core.quotePath false"
    echo "set -g mouse on" > "$HOME_DIR/.tmux.conf"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME_DIR/.bashrc"
    chown -R "${USERNAME}:${USERNAME}" "$HOME_DIR"
elif [ -d "$HOME_DIR" ] && [ "$(stat -c %u "$HOME_DIR")" != "$CONTAINER_UID" ]; then
    chown -R "${USERNAME}:${USERNAME}" "$HOME_DIR"
fi

# ============================================
# 런타임 requirements 설치
# ============================================
if [ -n "${EXTRA_REQUIREMENTS:-}" ]; then
    if [ -f "${EXTRA_REQUIREMENTS}" ]; then
        echo "==> 추가 패키지 설치: $EXTRA_REQUIREMENTS"
        pip install --no-cache-dir -q -r "$EXTRA_REQUIREMENTS"
    else
        echo "⚠️ EXTRA_REQUIREMENTS=${EXTRA_REQUIREMENTS} 파일이 존재하지 않습니다." >&2
    fi
fi

# ============================================
# Docker 환경변수를 SSH 세션에서도 사용할 수 있도록
# ============================================
{
    [ -n "${HF_TOKEN:-}" ] && echo "export HF_TOKEN=\"$HF_TOKEN\""
    true
} > /etc/profile.d/docker-env.sh

exec "$@"
