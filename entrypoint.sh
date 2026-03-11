#!/bin/bash
# 홈 디렉토리가 비어있으면 초기 설정 파일 복사
USERNAME=$(getent passwd 1000 | cut -d: -f1 2>/dev/null || echo "")
if [ -z "$USERNAME" ]; then
    USERNAME=$(ls /etc/skel/../home/ 2>/dev/null | head -1)
fi
HOME_DIR="/home/$USERNAME"

if [ -d "$HOME_DIR" ] && [ -z "$(ls -A $HOME_DIR 2>/dev/null)" ]; then
    cp -a /etc/skel/. "$HOME_DIR/" 2>/dev/null
    # Dockerfile에서 생성한 설정 파일 복원
    if [ -d "/etc/docker-home-init" ]; then
        cp -a /etc/docker-home-init/. "$HOME_DIR/"
    fi
    chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
fi

exec "$@"
