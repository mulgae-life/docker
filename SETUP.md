# dev-fullstack 컨테이너 설치 가이드

## 1. 사전 준비

```bash
# Docker 설치 (Ubuntu 기준)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 재로그인 후 docker 명령 사용 가능
```

## 2. 파일 준비

서버에 `docker/` 디렉토리를 복사한다.

```bash
# 방법 A: 레포 클론
git clone <repo-url> && cd <repo>/docker

# 방법 B: 파일만 복사
scp -r docker/ user@서버:/path/to/docker/
```

디렉토리 구조:
```
docker/
├── Dockerfile
├── docker-compose.yml
└── SETUP.md
```

## 3. 이미지 빌드

```bash
cd docker

# docker compose 사용 (권장)
docker compose build

# 또는 직접 빌드
docker build -t dev-fullstack .
```

빌드 시간: 약 10-15분 (네트워크 환경에 따라 다름)

### 사용자명/비밀번호 변경

```bash
docker compose build --build-arg USERNAME=myuser --build-arg PASSWORD=mypass
```

## 4. 컨테이너 실행

```bash
# docker compose 사용 (권장)
docker compose up -d

# 또는 직접 실행
docker run -d \
  --name dev-fullstack \
  --hostname dev-fullstack \
  --restart unless-stopped \
  -p 2222:22 \
  -p 3001:3001 \
  -p 8000:8000 \
  -v workspace:/workspace \
  dev-fullstack
```

## 5. 접속

```bash
# SSH 접속
ssh hjjo@서버IP -p 2222

# 컨테이너 직접 접속 (SSH 없이)
docker exec -it -u hjjo dev-fullstack bash
```

## 6. 프로젝트 셋업

```bash
# 컨테이너 내부에서
cd /workspace

# GitHub 인증
gh auth login

# 프로젝트 클론
gh repo clone <owner>/culture-calendar
gh repo clone <owner>/chatbot-poc

# culture-calendar
cd culture-calendar
cp .env.example .env        # 환경변수 편집
ln -s ../../.env apps/web/.env.local
pnpm install
pnpm dev                    # http://서버IP:3001

# chatbot-poc
cd /workspace/chatbot-poc
cp .env.example .env        # 환경변수 편집
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --host 0.0.0.0 --port 8000  # http://서버IP:8000
```

## 자주 쓰는 명령

```bash
# 컨테이너 상태 확인
docker compose ps

# 로그 확인
docker compose logs -f

# 컨테이너 중지/시작/재시작
docker compose stop
docker compose start
docker compose restart

# 이미지 재빌드 (Dockerfile 수정 후)
docker compose up -d --build

# 컨테이너 + 볼륨 완전 삭제 (주의: workspace 데이터 삭제됨)
docker compose down -v
```
