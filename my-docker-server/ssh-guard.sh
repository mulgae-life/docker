#!/usr/bin/env bash
# 컨테이너 SSH(호스트 5000=cfd, 5010=dev-fullstack)로 들어오는 브루트포스 봇을 sshd 앞에서 차단한다.
# - Tailscale·집 LAN은 무조건 통과
# - 그 외 출처는 IP당 10분에 20회 초과 시 차단 (두드리는 동안 차단 유지)
# 규칙이 이미 있으면 건너뛰므로 여러 번 실행해도 안전하다.
set -euo pipefail

PORTS=(5000 5010)
NAME=SSHCFD        # xt_recent 목록 이름 (두 포트가 공유; 이름을 바꾸면 기존 규칙과 중복되므로 유지)
CHAIN=DOCKER-USER

# DOCKER-USER 체인은 Docker가 만든다. 없으면 Docker가 아직 안 뜬 것이므로 잠시 기다린다.
for _ in $(seq 1 30); do
    iptables -S "$CHAIN" >/dev/null 2>&1 && break
    sleep 1
done
iptables -S "$CHAIN" >/dev/null 2>&1 || { echo "$CHAIN 체인이 없습니다 (Docker 미기동?)" >&2; exit 1; }

# 포트별 규칙 본문 (순서 중요: 통과 → 차단 → 카운트)
rules_for_port() {
    local port=$1
    echo "-p tcp -s 100.64.0.0/10 -m conntrack --ctorigdstport $port -j RETURN"
    echo "-p tcp -s 192.168.75.0/24 -m conntrack --ctorigdstport $port -j RETURN"
    echo "-p tcp -m conntrack --ctstate NEW --ctorigdstport $port -m recent --name $NAME --update --seconds 600 --hitcount 20 -j DROP"
    echo "-p tcp -m conntrack --ctstate NEW --ctorigdstport $port -m recent --name $NAME --set"
}

pos=1
for port in "${PORTS[@]}"; do
    while IFS= read -r rule; do
        # shellcheck disable=SC2086
        if ! iptables -C "$CHAIN" $rule 2>/dev/null; then
            # shellcheck disable=SC2086
            iptables -I "$CHAIN" "$pos" $rule
            echo "추가: $rule"
        fi
        pos=$((pos + 1))
    done < <(rules_for_port "$port")
done

iptables -S "$CHAIN"
