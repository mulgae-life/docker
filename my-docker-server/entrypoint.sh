#!/bin/bash
for INIT_DIR in /etc/docker-home-init/*/; do
    [ -d "$INIT_DIR" ] || continue
    USERNAME=$(basename "$INIT_DIR")
    HOME_DIR="/home/$USERNAME"

    if [ -d "$HOME_DIR" ] && [ -z "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
        # 홈 디렉토리가 비어있으면 초기 설정 파일 복사
        cp -a "$INIT_DIR/." "$HOME_DIR/"
        chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
    elif [ -d "$HOME_DIR" ] && [ "$(stat -c %u "$HOME_DIR")" != "$(id -u "$USERNAME")" ]; then
        # 홈 디렉토리 소유자 UID가 현재 사용자 UID와 불일치하면 chown
        chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
    fi
done

exec "$@"
