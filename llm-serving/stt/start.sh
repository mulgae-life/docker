#!/bin/bash
# ═══════════════════════════════════════════════════════
# STT 클러스터 진입점 — 본체는 ../vllm/start.sh (env-driven)
# ═══════════════════════════════════════════════════════
#
# 본 파일은 wrapper다. STT 디렉토리 정보(인스턴스/게이트웨이/로그 위치 + 라벨)만
# 환경변수로 export한 뒤 vllm/start.sh를 그대로 실행한다.
# 모든 운영 로직(up/down/status/restart, [name] 라우팅, confirm 프롬프트,
# fcntl 직렬화, atomic runtime write, cmdline PID 매칭)은 vllm/start.sh 단일 출처.
#
# 사용법은 vllm/start.sh와 동일:
#   ./start.sh up                # [y/N] 전체 적용 confirm 프롬프트
#   ./start.sh up all            # 전체 인스턴스 + 게이트웨이 기동
#   ./start.sh up voxtral        # instances/voxtral.yaml 단독 기동
#   ./start.sh up 5017           # gateways/5017.yaml 단독 기동
#   ./start.sh down              # [y/N] 전체 중지 confirm 프롬프트
#   ./start.sh status            # 상태 확인 ("STT 클러스터" 라벨로 출력)
# ═══════════════════════════════════════════════════════

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLUSTER_LABEL="STT"
export INSTANCES_DIR="$HERE/instances"
export GATEWAYS_DIR="$HERE/gateways"
export LOG_DIR="$HERE/logs"
exec bash "$HERE/../vllm/start.sh" "$@"
