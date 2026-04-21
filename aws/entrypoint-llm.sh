#!/bin/bash
set -euo pipefail

# ============================================
# 런타임 초기화
# - USERNAME=root: 운영계 모드 (사용자 생성 스킵, root 홈 /root 사용)
# - USERNAME=<일반>: 개발 모드 (사용자 생성 + 홈 셋업)
# - code-server 와 Claude 설치는 양쪽 모두 공통 수행
# ============================================
USERNAME="${USERNAME:-user}"
PASSWORD="${PASSWORD:-changeme}"
CONTAINER_UID="${CONTAINER_UID:-1000}"
CONTAINER_GID="${CONTAINER_GID:-1000}"
CODE_SERVER_PORT="${CODE_SERVER_PORT:-7777}"

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

# ============================================
# 런타임 requirements 설치 (vLLM 의존성 보강용)
# ============================================
if [ -n "${EXTRA_REQUIREMENTS:-}" ]; then
    if [ -f "${EXTRA_REQUIREMENTS}" ]; then
        echo "==> 추가 패키지 설치: $EXTRA_REQUIREMENTS"
        pip install --no-cache-dir --break-system-packages -q -r "$EXTRA_REQUIREMENTS"
    else
        echo "⚠️ EXTRA_REQUIREMENTS=${EXTRA_REQUIREMENTS} 파일이 존재하지 않습니다." >&2
    fi
fi

# ============================================
# 일반 사용자 생성 (root는 이미 있으므로 스킵)
# ============================================
if [ "$USERNAME" != "root" ]; then
    if ! id "$USERNAME" &>/dev/null; then
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

    # 홈 디렉토리 초기화 (bind mount 시 빈 디렉토리 복원)
    HOME_DIR="/home/$USERNAME"
    if [ -d "$HOME_DIR" ] && [ -z "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
        cp -a /etc/skel/. "$HOME_DIR/" 2>/dev/null || true
        setup_user_home "$HOME_DIR"
    elif [ -d "$HOME_DIR" ] && [ "$(stat -c %u "$HOME_DIR")" != "$CONTAINER_UID" ]; then
        chown -R "${USERNAME}:${USERNAME}" "$HOME_DIR"
    fi

    chown -R "${USERNAME}:${USERNAME}" /usr/local/nvm
fi

# ============================================
# Docker 환경변수 (SSH/code-server 셸 공통)
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

# 비로그인 bash 셸에서도 env 적용 (code-server 통합 터미널 대응)
if ! grep -q 'docker-env.sh' /etc/bash.bashrc 2>/dev/null; then
    echo '[ -f /etc/profile.d/docker-env.sh ] && . /etc/profile.d/docker-env.sh' >> /etc/bash.bashrc
fi

# ============================================
# 사용자 홈 디렉토리 동적 탐지
# (root: /root, 일반: /home/<user>)
# ============================================
USER_HOME=$(getent passwd "$USERNAME" | cut -d: -f6)
if [ -z "$USER_HOME" ] || [ ! -d "$USER_HOME" ]; then
    echo "❌ 사용자 '${USERNAME}' 홈 디렉토리를 찾을 수 없습니다." >&2
    exit 1
fi

# ============================================
# Claude Code 설치 (폐쇄망이면 실패해도 진행)
# ============================================
if [ ! -f "${USER_HOME}/.local/bin/claude" ]; then
    echo "==> Claude Code 설치 시도: ${USERNAME} (home: ${USER_HOME})"
    # timeout 필수: 폐쇄망 방화벽이 TCP SYN을 drop하면 기본 curl은 수 분간 재전송 대기 → 컨테이너 기동이 행 걸린 것처럼 보임
    su - "$USERNAME" -c "curl -fsSL --connect-timeout 5 --max-time 30 https://claude.ai/install.sh | bash" \
        || echo "⚠️ Claude Code 설치 실패 (폐쇄망이거나 네트워크 문제) — 무시하고 진행" >&2
fi

# ============================================
# code-server 설정 + 백그라운드 실행 (root/일반 공통)
# - 인증 패스워드는 SSH 패스워드(PASSWORD)와 동일
# - 확장은 이미지에 포함된 /opt/code-server-extensions 사용
# - telemetry 차단 (폐쇄망 대응)
# - YAML single-quote 이스케이프: !, :, #, 공백 등 특수문자 안전 처리
# ============================================
YAML_PASSWORD="${PASSWORD//\'/\'\'}"
mkdir -p "${USER_HOME}/.config/code-server"
cat > "${USER_HOME}/.config/code-server/config.yaml" <<EOF
bind-addr: 0.0.0.0:${CODE_SERVER_PORT}
auth: password
password: '${YAML_PASSWORD}'
cert: false
extensions-dir: /opt/code-server-extensions
disable-telemetry: true
disable-update-check: true
EOF
# 일반 사용자일 때만 소유권 이전 (root는 이미 root 소유)
if [ "$USERNAME" != "root" ]; then
    chown -R "${USERNAME}:${USERNAME}" "${USER_HOME}/.config"
fi

# code-server 시작 (자동 복구 없음 — 크래시는 docker healthcheck로 가시화,
# 복구는 관리자가 로그 확인 후 수동으로: docker exec <ctn> su - <user> -c 'code-server /workspace &')
if ! pgrep -u "$USERNAME" -f "code-server" >/dev/null 2>&1; then
    echo "==> code-server 실행 (user: ${USERNAME}, home: ${USER_HOME}, port: ${CODE_SERVER_PORT})"
    su - "$USERNAME" -c "nohup code-server /workspace > ${USER_HOME}/.code-server.log 2>&1 &"
fi

exec "$@"
