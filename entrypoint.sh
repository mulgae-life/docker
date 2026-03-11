#!/bin/bash
# 홈 디렉토리가 비어있으면 초기 설정 파일 복사
# /etc/docker-home-init/<USERNAME>/ → /home/<USERNAME>/

for INIT_DIR in /etc/docker-home-init/*/; do
    [ -d "$INIT_DIR" ] || continue
    USERNAME=$(basename "$INIT_DIR")
    HOME_DIR="/home/$USERNAME"

    if [ -d "$HOME_DIR" ] && [ -z "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
        cp -a "$INIT_DIR/." "$HOME_DIR/"
        chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
    fi
done

exec "$@"
