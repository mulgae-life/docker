#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# STT 호출 로그 동기화 진입점 — 본체는 ../vllm/logging.sh (env-driven)
# ─────────────────────────────────────────────────────────────────────
#
# 본 파일은 wrapper다. STT 작업 디렉토리(WORK_DIR), 기본 인스턴스(INST_DEFAULT),
# S3 prefix(S3_PREFIX)만 환경변수로 export한 뒤 vllm/logging.sh를 그대로 실행한다.
# 모든 sync 로직(5분 폴링, awk 일자 라우팅, S3 업로드, PID 관리)은 vllm/logging.sh 단일 출처.
#
# 사용법:
#   ./logging.sh              시작 (기본 인스턴스 'voxtral')
#   ./logging.sh whisper_v3   시작 (인스턴스 'whisper_v3')
#   ./logging.sh stop         중지
#
# 환경변수 (선택, 본 wrapper 또는 호출 시 override 가능):
#   S3_BUCKET  기본 hgi-ai-res
#   S3_PREFIX  기본 logs/stt              ← vllm은 logs/vllm, STT는 logs/stt로 분리
#   INTERVAL   기본 300                   ← 동기화 간격(초)
#
# STT 호출 매칭:
#   필터 'POST /v1/'는 /v1/audio/transcriptions(POST)에도 매칭된다.
#   /v1/realtime(WebSocket upgrade)은 매칭되지 않으므로 본 통계는 transcription 호출만 포함.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WORK_DIR="$HERE"
export INST_DEFAULT="voxtral"
export S3_PREFIX="${S3_PREFIX:-logs/stt}"
exec bash "$HERE/../vllm/logging.sh" "$@"
