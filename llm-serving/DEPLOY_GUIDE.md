# 🚀 llm-serving 배포 가이드

`llm-serving/` 코드를 **로컬 → S3 → 운영계 컨테이너**로 옮기고 모델을 띄우는 절차.

> 인프라 셋업(EC2/Docker/컨테이너 기동)은 [`../aws/SETUP_GUIDE.md`](../aws/SETUP_GUIDE.md). 본 문서는 그 위에서 **서빙 코드/모델만**.

---

## 흐름

```
[로컬]                          [S3]                                [운영계 컨테이너]
/workspace/docker/llm-serving   →  s3://hgi-ai-res/hjjo/            →  /workspace/llm-serving/
                                       llm-serving/                       └ ./start.sh

                                                                       /models/  ← 첫 기동 시 자동 다운로드
```

---

## 1. 로컬 → S3 (코드 업로드)

```bash
cd /workspace/docker
aws s3 sync ./llm-serving/ s3://hgi-ai-res/hjjo/llm-serving/ \
    --exclude "*/logs/*" --exclude "*/__pycache__/*" \
    --exclude "*/.vllm_serve_*" --exclude "*/samples/*"
```

> `logs/`, `__pycache__/`, 런처 임시 config(`.vllm_serve_*`)는 런타임 산출물이라 제외.

---

## 2. 운영계 → S3 다운로드

운영계 EC2 호스트에서 컨테이너 진입 후 작업:

```bash
docker exec -it gemma bash               # 컨테이너 이름은 환경에 맞게 (예: gemma, llm-root, jin)

# 컨테이너 안에서
cd /workspace/
sudo aws s3 sync s3://hgi-ai-res/hjjo/llm-serving/ ./llm-serving/
sudo chmod +x llm-serving/*/start.sh
```

> 컨테이너 이름 확인: `docker ps`. user.sh 로 띄운 컨테이너는 이름 그대로(`gemma` 등), 메인 compose 는 `llm-<USERNAME>`.

---

## 3. 모델 띄우기

```bash
# vLLM (LLM) — instances/*.yaml + gateways/*.yaml 자동 순회
cd /workspace/llm-serving/vllm
./start.sh up                # 전체 인스턴스 + 게이트웨이 기동
./start.sh up gemma          # 단일 인스턴스만 기동 (instances/gemma.yaml)
./start.sh status            # UP 확인 (1~5분 소요, 모델 미보유 시 자동 다운로드 → /models/LLM/)

# STT
cd /workspace/llm-serving/stt
./start.sh
./start.sh status            # 모델 미보유 시 자동 다운로드 → /models/STT/
```

> ⚠️ **LLM ↔ STT 동시 운영 주의**: 현재 `vllm/instances/{gemma,qwen}.yaml` 이 각각 GPU 0/1 점유. STT 테스트 시 LLM 인스턴스 먼저 stop 필요 (`cd vllm && ./start.sh down`). 상세는 [`stt/README.md`](stt/README.md) "운영 주의".

---

## 4. 코드 변경 반영

```bash
# (로컬) 수정 후 S3 재업로드
cd /workspace/docker
aws s3 sync ./llm-serving/ s3://hgi-ai-res/hjjo/llm-serving/ \
    --exclude "*/logs/*" --exclude "*/__pycache__/*" \
    --exclude "*/.vllm_serve_*" --exclude "*/samples/*"

# (운영계) 재다운로드 + 재시작
cd /workspace/
sudo aws s3 sync s3://hgi-ai-res/hjjo/llm-serving/ ./llm-serving/
cd llm-serving/vllm && ./start.sh restart        # 또는 stt (단일 재시작은 ./start.sh restart <name>)
```

> 로컬에서 파일을 삭제했다면 운영계에 잔존하므로 `--delete` 추가. 처음에는 `--dryrun` 으로 확인 권장.

---

## 5. 트러블슈팅

| 증상 | 해결 |
|------|------|
| `aws: command not found` (운영계) | 컨테이너에 aws CLI 미설치. 호스트에서 `sudo aws s3 sync … /volume/workspace/<USERNAME>/llm-serving/` 후 컨테이너 안에서 작업 |
| `Unable to locate credentials` | EC2 IAM Role 미부여 또는 `aws configure` 미실행 |
| `Permission denied: ./start.sh` | `sudo chmod +x llm-serving/*/start.sh` |
| 모델 다운로드 401/403 | gated 모델 + HF_TOKEN 미설정. `~/aws/.env` 의 `HF_TOKEN` 확인 후 `docker compose up -d --force-recreate` |
| 모델 다운로드 timeout (폐쇄망) | EC2 외부망 차단. 외부망 PC에서 사전 다운로드 → S3 → `/volume/models/` 로 이관. 절차는 [`vllm/VLLM_OPS_GUIDE.md`](vllm/VLLM_OPS_GUIDE.md) §4.2 참조 |
| 코드 수정이 반영 안 됨 | `__pycache__` 캐시. `find /workspace/llm-serving -name __pycache__ -exec rm -rf {} +` 후 재시작 |
| GPU OOM | `vllm/instances/<name>.yaml` 의 `gpu_memory_utilization` 낮추기, 또는 다른 인스턴스 stop (`./start.sh down <name>`) |

---

## 참고

- 인프라/컨테이너: [`../aws/SETUP_GUIDE.md`](../aws/SETUP_GUIDE.md)
- vLLM 운영 상세 (모델 교체, 메모리 표 등): [`vllm/VLLM_OPS_GUIDE.md`](vllm/VLLM_OPS_GUIDE.md)
- STT PoC: [`stt/README.md`](stt/README.md), [`stt/MODEL_STUDY.md`](stt/MODEL_STUDY.md)
