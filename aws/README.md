# AWS EC2 GPU 서버 (vLLM + 다중 사용자)

Amazon Linux 2023 + NVIDIA GPU EC2에 vLLM 기반 LLM 환경을 1회 셋업으로 띄우는 Docker 인프라 템플릿.

**제공 기능**
- vLLM 베이스 컨테이너 + SSH + 브라우저 IDE(code-server)
- 다중 사용자 컨테이너 자동 관리 (`user.sh`, 포트/홈 자동 할당)
- 폐쇄망 대응 (SSM 포트 포워딩, VS Code 확장 사이드로드)
- 한 인스턴스 = 한 모드 (`MODE=dev` 또는 `MODE=prd`)

---

## 1. 사전 준비

| 항목 | 내용 |
|------|------|
| EC2 인스턴스 | NVIDIA GPU 탑재 (g6e/p4/p5 등) |
| OS | Amazon Linux 2023 |
| 추가 EBS | (선택) 데이터/모델 영속화용. 연결 시 `lsblk`로 디바이스 경로 확인(예: `/dev/nvme1n1`). 미연결 시 루트 디스크에 `/volume` 디렉토리만 생성 ⚠️ 인스턴스 종료 시 손실 |
| 권한 | `sudo` 가능한 OS 계정 |
| AWS CLI | EC2 호스트에서 S3 접근 가능 (IAM Role 또는 `aws configure`) |
| (선택) | HuggingFace 토큰 (게이트 모델 사용 시) |

> 💡 **운영 환경**: 본 인프라는 **내부망 전용**입니다. `.env`의 `HF_TOKEN`/`PASSWORD` 등 시크릿은 내부 정책으로 보호되므로 S3/EBS 동기화 시에도 별도 마스킹하지 않습니다.

---

## 2. 모드 선택: dev vs prd

| 항목 | `MODE=dev` | `MODE=prd` |
|------|:----------:|:----------:|
| 용도 | 개발/실험 | 챗봇/서빙 |
| Claude Code 자동 설치 | ✅ | ❌ |
| nvm + Node + codex 빌드 | ✅ | ❌ |
| 이미지 슬림화 | ❌ | ✅ |
| 기본 `USERNAME` | `user` | `root` |
| 기본 `LLM_MEMORY` | `48g` | `48g` |
| 기본 `EXTRA_REQUIREMENTS` | `/data/requirements.txt` | `/data/requirements.txt` |
| 기본 이미지 태그 | `llm-dev` | `llm-prd` |

> 인스턴스 단위로 모드를 결정합니다. 한 인스턴스에 두 모드를 공존시키지 않습니다.

---

## 3. 빠른 시작

### 3-1. 로컬 → S3 (코드 변경 시)
```bash
cd /workspace/docker/aws
aws s3 sync . s3://hgi-ai-res/hjjo/aws/
```

### 3-2. EC2 인스턴스 최초 셋업
```bash
# (1) 코드 다운로드
mkdir -p ~/aws && aws s3 sync s3://hgi-ai-res/hjjo/aws/ ~/aws/
cd ~/aws

# (2) 환경 모드 선택 → .env 생성
cp .env.dev.example .env       # 개발 EC2
# 또는
cp .env.prd.example .env       # 운영 EC2

vim .env                       # USERNAME, PASSWORD, VOLUME_DEVICE, HF_TOKEN 등 입력

# (3) 호스트 셋업 (Phase 1 → 자동 reboot → Phase 2 자동 실행)
chmod +x setup-ec2.sh user.sh
sudo ./setup-ec2.sh
tail -f /var/log/ec2-setup.log    # 진행 확인 (다른 터미널)

# (4) 컨테이너 빌드 + 기동
docker compose build
docker compose up -d
docker compose logs -f llm        # 로그 확인
```

---

## 4. `.env` 주요 키

| 키 | 설명 | 필수 |
|----|------|:----:|
| `MODE` | `dev` \| `prd` (모드 분기 트리거) | ✅ |
| `USERNAME` | OS 계정명 (prd는 `root`) | ✅ |
| `PASSWORD` | SSH + code-server 패스워드 | ✅ |
| `VOLUME_DEVICE` | EBS 디바이스 경로 (`lsblk`). **비우면 루트 디스크에 `/volume` 생성** (운영계 + S3 재동기화 정책 시) | — |
| `VOLUME_PATH` | EBS 마운트 경로 (기본 `/volume`) | — |
| `HF_TOKEN` | HuggingFace 토큰 | 게이트 모델 시 |
| `LLM_GPUS` | `all` 또는 `0,1` 등 | ✅ |
| `LLM_MEMORY` | 컨테이너 메모리 한도 | ✅ |
| `LLM_SSH_PORT` | 호스트 SSH 매핑 포트 (기본 5000) | 충돌 시 |
| `LLM_EXTRA_PORTS` | 서비스용 추가 포트 (단일/range) | — |
| `LLM_CODE_SERVER_PORT` | code-server 포트 (기본 5500) | 충돌 시 |
| `EXTRA_REQUIREMENTS` | 컨테이너 내 pip 추가 설치 경로 | 선택 |
| `VLLM_IMAGE` | vLLM 베이스 이미지 (버전 업그레이드 시 변경) | — |

---

## 5. `setup-ec2.sh`가 하는 일

| Phase 1 (재부팅 전) | Phase 2 (재부팅 후 자동) |
|---------------------|--------------------------|
| OS 사용자 + sudo 권한 | NVIDIA Container Toolkit |
| SSH + fail2ban (포트 5555) | Fabric Manager (H100/H200/A100/B100/B200 자동 감지) |
| EBS 포맷 + 마운트 + fstab 등록 (디바이스 미지정 시 `/volume` 디렉토리만 생성) | Docker GPU 동작 검증 |
| `/volume/{workspace,homes,models,data}` 생성 | |
| Docker + Compose V2 + Buildx | |
| NVIDIA 오픈 드라이버 (`nvidia-open`) | |
| Claude Code 호스트 설치 (dev만) | |

> Phase 1 끝나면 자동 `reboot`. Phase 2는 `systemd` 서비스로 자동 실행. 실패 시 서비스가 자동 정리됩니다(리부트 루프 방지).

---

## 6. 다중 사용자 컨테이너 (`user.sh`)

`user.sh`로 사용자별 독립 컨테이너를 생성합니다. 포트는 자동 할당 (10포트씩).

```bash
# 일반 개발 사용자 (SSH + code-server, 동명 OS 계정 자동 생성)
sudo ~/aws/user.sh up jin    --password 1234 --gpus 2,3
sudo ~/aws/user.sh up song   --password 1234 --gpus 0,1
sudo ~/aws/user.sh up cho    --password 1234 --gpus 0

# 운영 root 컨테이너 (SSH 불가, code-server 전용)
sudo ~/aws/user.sh up prd-job --root --password aiteam12 --gpus all

# 다중 root 컨테이너 (서비스별 분리 — 한 EC2에서 동시 운영)
sudo ~/aws/user.sh up service-a --root --service-port 5031 --code-port 5510 --gpus 0,1
sudo ~/aws/user.sh up service-b --root --service-port 5015 --code-port 5520 --gpus 2,3

sudo ~/aws/user.sh list                  # 컨테이너 목록
sudo ~/aws/user.sh down jin              # 중지 + 제거 (데이터는 /volume 보존)
sudo ~/aws/user.sh rebuild               # 이미지 변경 후 전체 재생성
sudo ~/aws/user.sh rebuild jin           # 특정 사용자만
```

### 포트 자동 할당 규칙

| 컨테이너 | SSH 포트 | 추가 포트 |
|----------|:--------:|:---------:|
| 메인 (`docker compose`) | 5000 | 5001-5009 |
| `user.sh` 첫 사용자 | 5010 | 5011-5019 |
| `user.sh` 두 번째 | 5020 | 5021-5029 |
| ... | ... | ... |
| `user.sh` 49번째 | 5490 | 5491-5499 |

---

## 7. 접속

### 7-1. SSH
```bash
ssh -p <PORT> <user>@<host>     # PORT = LLM_SSH_PORT 또는 user.sh 할당 포트
```

`~/.ssh/config` 예시:
```
Host AWS-main
    HostName <host>
    Port 5000
    User <user>

Host AWS-jin
    HostName <host>
    Port 5010
    User jin
```

### 7-2. code-server (브라우저 IDE)

폐쇄망 EC2는 SSM 포트 포워딩으로 접속:

```bash
aws ssm start-session \
  --target i-<INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5500"],"localPortNumber":["8443"]}'
```

브라우저에서 `http://localhost:8443` (패스워드 = `.env`의 `PASSWORD`).

| 컨테이너 | code-server 포트 |
|----------|:----------------:|
| 메인 | `LLM_CODE_SERVER_PORT` (기본 5500) |
| `user.sh` 사용자 | 각 포트 범위의 첫 포트 (5011 / 5021 / …) |

---

## 8. 운영 환경 (prd) 별도 안내

```bash
cp .env.prd.example .env       # MODE=prd, USERNAME=root 기본
vim .env                       # PASSWORD, HF_TOKEN, EXTRA_REQUIREMENTS 등
sudo ./setup-ec2.sh
docker compose up -d
```

- **이미지 슬림화**: nvm/Node/codex 빌드 안 됨, Claude Code 미설치
- **`USERNAME=root`**: OS 사용자 생성 스킵, `/root` 홈 사용
- **`/root` 영속화**: 호스트 `/volume/root`에 마운트 (bash history, code-server 설정 보존)
- **SSH 접속 불가** → SSM 포트 포워딩으로 code-server 접근만 가능 (§7-2)
- **다중 운영계 슬롯**: `user.sh --root`는 `--service-port`/`--code-port` 인자로 컨테이너별 호스트 포트를 분리할 수 있어 한 EC2에서 다수 동시 운용 가능. 인자 미지정 시 `.env`(`LLM_EXTRA_PORTS`/`LLM_CODE_SERVER_PORT`) 폴백이라 compose `llm-root`와 같은 포트가 되어 충돌하므로, **다중 운용 시 인자 명시 필수**. 홈은 `/volume/root-homes/<name>`에 컨테이너별 독립 보존, `rebuild` 시 라벨에 저장된 포트가 그대로 복원됨.

---

## 9. 유지보수

### 9-1. 코드 변경 반영
```bash
# (1) 로컬 → S3
cd /workspace/docker/aws
aws s3 sync . s3://hgi-ai-res/hjjo/aws/

# (2) EC2 → 다운로드 + 재빌드
cd ~/aws
aws s3 sync s3://hgi-ai-res/hjjo/aws/ ~/aws/
chmod +x setup-ec2.sh user.sh
docker compose build --no-cache && docker compose up -d
sudo ~/aws/user.sh rebuild              # 사용자 컨테이너 일괄 갱신
```

### 9-2. `.env`만 수정한 경우 (이미지 재빌드 불필요)
```bash
docker compose up -d --force-recreate
sudo ~/aws/user.sh rebuild              # 이미지 갱신 + 기존 옵션 보존 재생성
```

> ⚠️ `user.sh rebuild`는 기존 컨테이너의 `PASSWORD`/`GPU`/`MODE` 등을 그대로 복원합니다. **비밀번호·GPU 할당·모드 변경**은 `down` → `up` 사이클이 필요합니다:
> ```bash
> sudo ~/aws/user.sh down jin
> sudo ~/aws/user.sh up jin --password new_pw --gpus 0,1
> ```

### 9-3. VS Code 확장 추가 (폐쇄망)
1. 외부 PC에서 Marketplace → `.vsix` 다운로드
2. `~/aws/vsix/` 폴더에 `.vsix` 파일 넣기
3. `docker compose build --no-cache` (빌드 타임에 자동 설치, `/opt/code-server-extensions`에 영속)

---

## 10. 디렉토리 구조 (`/volume`)

```
/volume/
├── workspace/<user>     # 사용자별 작업 공간 (컨테이너 /workspace)
├── homes/<user>         # 사용자 홈 (컨테이너 /home/<user>)
├── root-homes/<name>    # user.sh --root 컨테이너 홈
├── root                 # 메인 운영계 root 홈 (USERNAME=root 일 때)
├── models               # 모델 저장소
└── data                 # 데이터
```

> 컨테이너를 `down`/`rm`해도 `/volume`은 보존됩니다.

---

## 11. 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-------------|
| `setup-ec2.sh: 디바이스 또는 자식 파티션이 ... 마운트됨` 에러 | 루트/시스템 디스크 또는 자식 파티션이 이미 사용 중. `lsblk`로 `/`나 `/boot/efi`가 안 붙은 추가 EBS만 지정 (디스크 전체 `/dev/nvme0n1` 입력 시에도 자식 `p1`이 `/`에 붙어 있으면 차단됨). 추가 EBS가 없는 운영계라면 `VOLUME_DEVICE`를 비워두면 됨 |
| `setup-ec2.sh: 기존 파티션이 존재합니다` 에러 | 기존 데이터 EBS가 파티션 형태(예: `/dev/nvme1n1p1`)로 데이터를 보유 중인데 `.env`에 디스크 전체 경로(`/dev/nvme1n1`)를 지정하여 발생. 자식 데이터 파괴 방지 가드 → 파티션 경로(`/dev/nvme1n1p1`)를 직접 입력하거나 새 EBS 사용 |
| `setup-ec2.sh: 사용자 UID/GID 불일치` 에러 | 호스트 기존 사용자 UID와 `.env CONTAINER_UID`가 다름 (예: 구버전 1001로 셋업된 EC2에 신버전 2000 적용). **데이터 보존 권장**: `.env`의 `CONTAINER_UID`/`GID`를 에러 메시지의 기존 UID 값으로 맞춘 후 재실행 → `/volume` 데이터 그대로 유지 |
| `setup-ec2.sh: Docker GPU 테스트 실패` 에러 | `systemctl restart docker` 후 `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi` 수동 재실행. 정상이면 `sudo ./setup-ec2.sh --phase2` 재시도 |
| `nvidia-smi` 실패 | Phase 2 미완료. `tail -f /var/log/ec2-setup.log` 확인 후 `sudo systemctl status ec2-setup-phase2.service` |
| code-server 접속 시 패스워드 거부 | `.env` `PASSWORD` 변경 후 `docker compose up -d --force-recreate` |
| `user.sh up` 시 포트 범위 초과 | 사용자 컨테이너가 49개 도달. 미사용 사용자 `down`으로 정리 |
| Claude Code 설치 실패 (폐쇄망) | dev 모드에서만 시도, 실패해도 컨테이너는 정상 기동. 수동 설치: `curl -fsSL https://claude.ai/install.sh \| bash` |
| 컨테이너 healthcheck unhealthy | code-server가 죽음. `docker logs llm-<user>` 확인 후 `docker exec <ctn> su - <user> -c 'code-server /workspace &'` 수동 재기동 |
| Fabric Manager 자동 설치 실패 | NVSwitch GPU(H100/H200 등)에서 발생 가능. 로그의 안내대로 수동 설치: `dnf module install -y nvidia-driver:<branch>-open/fm` |
| 빌드 시 `transformers` 충돌 | `requirements.txt` 마지막에 `transformers`를 `--no-deps`로 설치하므로 vLLM 핀과 충돌 안 함 (의도된 설계) |
