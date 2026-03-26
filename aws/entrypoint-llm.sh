#!/bin/bash

# 홈 디렉토리 초기화 (bind mount 시 빈 디렉토리 복원)
for INIT_DIR in /etc/docker-home-init/*/; do
    [ -d "$INIT_DIR" ] || continue
    USERNAME=$(basename "$INIT_DIR")
    HOME_DIR="/home/$USERNAME"

    if [ -d "$HOME_DIR" ] && [ -z "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
        cp -a "$INIT_DIR/." "$HOME_DIR/"
        chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
    elif [ -d "$HOME_DIR" ] && [ "$(stat -c %u "$HOME_DIR")" != "$(id -u "$USERNAME")" ]; then
        chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
    fi
done

# 런타임 requirements 설치 (환경변수로 경로 지정 시)
if [ -n "$EXTRA_REQUIREMENTS" ] && [ -f "$EXTRA_REQUIREMENTS" ]; then
    echo "==> 추가 패키지 설치: $EXTRA_REQUIREMENTS"
    pip install --no-cache-dir -q -r "$EXTRA_REQUIREMENTS"
fi

exec "$@"
