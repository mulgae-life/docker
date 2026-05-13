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
aws s3 sync /workspace/docker/llm-serving/ s3://hgi-ai-res/hjjo/llm-serving/ \
    --exclude "*/logs/*" --exclude "*/__pycache__/*" \
    --exclude "*/.vllm_serve_*" --exclude "*/samples/*" \
    --exclude "*/.archive/*"
```

> `logs/`, `__pycache__/`, 런처 임시 config(`.vllm_serve_*`)는 런타임 산출물이라 제외.

---

## 2. 운영계 → S3 다운로드

운영계 EC2 호스트에서 컨테이너 진입 후 작업:

```bash
docker exec -it gemma bash               # 컨테이너 이름은 환경에 맞게 (예: gemma, llm-root, jin)

# 컨테이너 안에서
sudo aws s3 sync s3://hgi-ai-res/hjjo/llm-serving/ /workspace/llm-serving/
sudo chmod +x /workspace/llm-serving/*/start.sh
```

> 컨테이너 이름 확인: `docker ps`. user.sh 로 띄운 컨테이너는 이름 그대로(`gemma` 등), 메인 compose 는 `llm-<USERNAME>`.

---

## 3. 모델 띄우기

```bash
# vLLM (LLM) — instances/*.yaml + gateways/*.yaml 자동 순회
cd /workspace/llm-serving/vllm
./start.sh up all            # 전체 인스턴스 + 게이트웨이 기동 (확인 없이)
./start.sh up                # 동일하지만 [y/N] 전체 적용 confirm 프롬프트가 먼저 뜸
./start.sh up gemma          # 단일 인스턴스만 기동 (instances/gemma.yaml)
./start.sh status            # UP 확인 (1~5분 소요, 모델 미보유 시 자동 다운로드 → /models/LLM/)

# STT — vllm 패턴 동일 (instances/voxtral.yaml ↔ gateways/5017.yaml 페어 자동)
cd /workspace/llm-serving/stt
./start.sh up all            # voxtral(:7172) + 게이트웨이(:5017) 자동 기동 (확인 없이)
./start.sh up                # 동일하지만 [y/N] 전체 적용 confirm 프롬프트가 먼저 뜸
./start.sh up voxtral        # voxtral 인스턴스만 단독 기동
./start.sh up 5017           # 5017 게이트웨이만 단독 기동
./start.sh status            # UP/STARTING 확인 (모델 미보유 시 자동 다운로드 → /models/STT/, 17GB)
```

> ⚠️ **LLM ↔ STT 동시 운영 주의**: voxtral 은 GPU 2 단독이라 LLM(`vllm/instances/{gemma,qwen}.yaml` GPU 0/1)과 충돌 없음. 단 비교용 `qwen3_asr / whisper_v3` 는 GPU 0/1 사용이라 LLM 먼저 stop 필요 (`cd vllm && ./start.sh down <name>` — 다른 모델 영향 방지를 위해 이름 명시 권장; 무인자는 [y/N] 전체 중지 프롬프트). 상세는 [`stt/README.md`](stt/README.md) "운영 주의".

> ⚠️ **Voxtral 의존성**: 운영계 컨테이너 재배포 시 `pip install soundfile soxr librosa` 필요. 영구 등재는 `aws/requirements.txt`. 미설치 시 vLLM 기동 직후 `EngineCore failed to start` + `ImportError: soundfile` 로 fail.

---

## 4. 동작 확인 (테스트 / 로그)

```bash
cd /workspace/llm-serving/vllm

# QA 테스트 — 모델명은 /v1/models API에서 자동 추출 (--model 불필요)
python tests/test_vllm_server.py --base-url http://localhost:5015
python tests/test_vllm_server.py --base-url http://localhost:5015 --category infra inference   # 일부만
python tests/test_vllm_server.py --list                                                         # 카테고리 목록

# 속도 비교 테스트 (게이트웨이별로 호출 — 같은 results 파일에 모델명 자동 추출하여 누적 append)
python tests/speed_test.py --base-url http://localhost:5015                  # Gemma 게이트웨이
python tests/speed_test.py --base-url http://localhost:5016                  # Qwen 게이트웨이 (같은 파일에 이어 쌓임)
python tests/speed_test.py --base-url http://localhost:5015 --quick          # 빠른 검증 (동시성 1, short, 200자)

# 로그 — 매 실행마다 tests/logs/test_YYYYMMDD_HHMMSS.log에 자동 저장 (ANSI 색 제거)
ls -lt tests/logs/test_*.log | head -3
grep -B1 -A20 "FAIL " tests/logs/test_20260430_144909.log     # 실패만 추출
```

> 실패/예외 시 detail에 **요청 method·URL·body + 응답 status·body + (예외 시) 전체 traceback**이 자동 부착됩니다. 상세 우선순위/포맷은 [`VLLM_OPS_GUIDE.md`](VLLM_OPS_GUIDE.md) §14.1.

---

## 5. 코드 변경 반영

```bash
# (로컬) 수정 후 S3 재업로드
cd /workspace/docker
aws s3 sync ./llm-serving/ s3://hgi-ai-res/hjjo/llm-serving/ \
    --exclude "*/logs/*" --exclude "*/__pycache__/*" \
    --exclude "*/.vllm_serve_*" --exclude "*/samples/*" \
    --exclude "*/.archive/*"

# (운영계) 재다운로드 + 재시작
cd /workspace/
sudo aws s3 sync s3://hgi-ai-res/hjjo/llm-serving/ ./llm-serving/
cd llm-serving/vllm && ./start.sh restart <name>  # 또는 stt; 무인자는 [y/N] 전체 재시작 프롬프트, 'all'은 확인 없이 전체
```

> 로컬에서 파일을 삭제했다면 운영계에 잔존하므로 `--delete` 추가. 처음에는 `--dryrun` 으로 확인 권장.

---

## 6. 트러블슈팅

| 증상 | 해결 |
|------|------|
| `aws: command not found` (운영계) | 컨테이너에 aws CLI 미설치. 호스트에서 `sudo aws s3 sync … /volume/workspace/<USERNAME>/llm-serving/` 후 컨테이너 안에서 작업 |
| `Unable to locate credentials` | EC2 IAM Role 미부여 또는 `aws configure` 미실행 |
| `Permission denied: ./start.sh` | `sudo chmod +x llm-serving/*/start.sh` |
| 모델 다운로드 401/403 | gated 모델 + HF_TOKEN 미설정. `~/aws/.env` 의 `HF_TOKEN` 확인 후 `docker compose up -d --force-recreate` |
| 모델 다운로드 timeout (폐쇄망) | EC2 외부망 차단. 외부망 PC에서 사전 다운로드 → S3 → `/volume/models/` 로 이관. 절차는 [`VLLM_OPS_GUIDE.md`](VLLM_OPS_GUIDE.md) §8.2 참조 |
| 코드 수정이 반영 안 됨 | `__pycache__` 캐시. `find /workspace/llm-serving -name __pycache__ -exec rm -rf {} +` 후 재시작 |
| GPU OOM | `vllm/instances/<name>.yaml` 의 `gpu_memory_utilization` 낮추기, 또는 다른 인스턴스 stop (`./start.sh down <name>`) |

---

## 참고

- 인프라/컨테이너: [`../aws/SETUP_GUIDE.md`](../aws/SETUP_GUIDE.md)
- vLLM API 호출 (사용자): [`VLLM_API_GUIDE.md`](VLLM_API_GUIDE.md)
- vLLM 운영 상세 (모델 교체, 메모리 표 등): [`VLLM_OPS_GUIDE.md`](VLLM_OPS_GUIDE.md)
- STT API 호출 (사용자): [`STT_API_GUIDE.md`](STT_API_GUIDE.md)
- STT 운영 (시스템 구조, 메모리 핏, 트러블슈팅): [`STT_OPS_GUIDE.md`](STT_OPS_GUIDE.md)
- STT 모델 비교 / PoC: [`stt/README.md`](stt/README.md), [`stt/MODEL_STUDY.md`](stt/MODEL_STUDY.md)
