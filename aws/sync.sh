#!/usr/bin/env bash
# aws/ 인프라 코드 S3 동기화 (로컬 ↔ S3)
# 사용법: ./sync.sh {push|pull} [추가 aws s3 sync 옵션...]
#
# 재빌드/재기동은 이 스크립트 책임이 아니다 (SRP):
#   - 이미지 재빌드: docker compose build  (SETUP_GUIDE.md §9-1)
#   - 인스턴스 재생성: ./user.sh rebuild <name>
# 여기선 코드 동기화만 하고, 완료 후 다음 단계 명령을 안내한다.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# =====================================================================
# S3 배포 설정 (SETUP_GUIDE.md §3-1 참조)
# 경로는 env로 오버라이드 가능 — 하드코딩 대신 안전한 기본값 + 설정 분리.
# =====================================================================
S3_URI="${AWS_INFRA_S3_URI:-s3://hgi-ai-res/hjjo/aws/}"

# push(로컬→S3) / pull(S3→로컬) 공통 제외 목록.
# ※ wheels/ 는 제외하지 않는다 — 246MB nightly wheel이지만 Dockerfile.llm이 COPY로
#    빌드 타임에 쓰므로 S3에는 반드시 올라가야 한다 (git에서만 .gitignore로 제외).
# ※ .env 는 동기화에 포함 — 내부망 전용 정책(시크릿 마스킹 없이 S3 보관).
SYNC_EXCLUDES=(
    --exclude '.git/*'
    --exclude '**/__pycache__/*'
    --exclude '*.pyc'
    --exclude '.archive/*'
    --exclude '.claude/*'
    --exclude '*.log'
)

require_aws() {
    if ! command -v aws > /dev/null 2>&1; then
        echo "[$1] aws CLI를 찾을 수 없습니다. AWS CLI 설치 또는 IAM Role/aws configure 설정이 필요합니다."
        exit 1
    fi
}

# 로컬 → S3 업로드 (개발 머신에서 코드 변경 반영)
cmd_push() {
    require_aws push

    echo "[push] 로컬 → S3 업로드: $SCRIPT_DIR → $S3_URI"
    if ! aws s3 sync "$SCRIPT_DIR" "$S3_URI" "${SYNC_EXCLUDES[@]}" "$@"; then
        echo "[push] S3 업로드 실패 — 경로/권한(IAM Role) 확인 후 재시도"
        exit 1
    fi
    echo "[push] 업로드 완료 (.env·wheels/ 포함 — 내부망 전용 정책)"
    echo "[push] 운영계 적용: ./sync.sh pull 후 docker compose build && ./user.sh rebuild <name>"
}

# S3 → 로컬 다운로드 (EC2 호스트에서 코드 받기)
cmd_pull() {
    require_aws pull

    echo "[pull] S3 → 로컬 다운로드: $S3_URI → $SCRIPT_DIR"
    if ! aws s3 sync "$S3_URI" "$SCRIPT_DIR" "${SYNC_EXCLUDES[@]}" "$@"; then
        echo "[pull] S3 다운로드 실패 — 경로/권한(IAM Role) 확인 후 재시도"
        exit 1
    fi
    chmod +x "$SCRIPT_DIR"/*.sh 2>/dev/null || true
    echo "[pull] 다운로드 완료"
    echo "[pull] 다음 단계: docker compose build && ./user.sh rebuild <name>"
}

case "${1:-}" in
    push)   shift; cmd_push "$@" ;;
    pull)   shift; cmd_pull "$@" ;;
    *)
        echo "사용법: $0 {push|pull} [추가 aws s3 sync 옵션...]"
        echo ""
        echo "  push   로컬 → S3 업로드 (개발 머신)"
        echo "  pull   S3 → 로컬 다운로드 (EC2 호스트)"
        echo ""
        echo "추가 옵션 예시:"
        echo "  ./sync.sh push --dryrun        업로드 미리보기 (실제 전송 안 함)"
        echo "  ./sync.sh push --delete        로컬에서 지운 파일을 S3에서도 삭제"
        exit 1
        ;;
esac
