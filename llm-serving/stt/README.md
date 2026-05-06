# 🎙️ STT 서빙 (vLLM 기반, 페어 구조)

`llm-serving/vllm/` 의 `instances/` + `gateways/` 패턴을 STT에 그대로 적용.
**Voxtral-Mini-4B-Realtime-2602** 를 게이트웨이 `:5017` 로 외부 노출하고,
`vllm_gateway.py` 본체가 `/v1/audio/transcriptions` (HTTP) + `/v1/realtime` (WebSocket) 라우트를 제공한다.

> 사용자(API 호출)용: [`../STT_API_GUIDE.md`](../STT_API_GUIDE.md)
> 운영자(서버 기동·튜닝)용: [`../STT_OPS_GUIDE.md`](../STT_OPS_GUIDE.md)
> 후보 모델 비교 / 시나리오: [`MODEL_STUDY.md`](MODEL_STUDY.md)

---

## 📦 구성

| 인스턴스 | 모델 | GPU | 내부 포트 | 외부 게이트웨이 | 상태 |
|---------|------|:---:|:----:|:----:|:----:|
| `voxtral` | `mistralai/Voxtral-Mini-4B-Realtime-2602` | 2 | 7172 | **5017** | ✅ 운영 |
| `qwen3_asr` | `Qwen/Qwen3-ASR-1.7B` | 0 | 7170 | (직접 노출) | 🧪 비교 PoC |
| `whisper_v3` | `openai/whisper-large-v3` | 1 | 7171 | (직접 노출) | 🧪 비교 PoC |

`qwen3_asr` / `whisper_v3` 는 `gateway_port` 메타가 없으므로 게이트웨이 디스커버리에 포함되지 않고 자기 포트(7170/7171)로 직접 노출됩니다 (한국어 정성 비교 PoC 용도).

```
llm-serving/stt/
├── README.md                # 본 문서
├── MODEL_STUDY.md           # 후보 모델 비교 / 시나리오
├── start.sh                 # vllm/start.sh 패턴 풀 도입 (instances/+gateways/ 페어 자동 순회)
├── instances/
│   ├── voxtral.yaml         # gateway_port: 5017 → :5017 페어, 내부 :7172
│   ├── qwen3_asr.yaml       # 비교용 (gateway_port 없음, :7170 직접)
│   └── whisper_v3.yaml      # 비교용 (gateway_port 없음, :7171 직접)
├── gateways/
│   └── 5017.yaml            # discover_from: ../instances, warmup 비활성화, timeout 600s
└── logs/                    # 인스턴스/게이트웨이 stdout/stderr (자동 생성)
```

> `vllm_server_launcher.py`, `vllm_gateway.py` 는 `../vllm/` 의 본체를 재사용 (코드 단일 출처). `start.sh` 만 STT 변종으로 분리.

---

## 🚀 사용법

```bash
cd llm-serving/stt

./start.sh up                       # 전체 인스턴스 + 게이트웨이 기동
./start.sh up voxtral               # voxtral 단독 (게이트웨이 미터치)
./start.sh up 5017                  # 5017 게이트웨이 단독 (인스턴스 미터치)
./start.sh status                   # UP/DOWN/STARTING/STALE 표시
./start.sh down voxtral             # voxtral 단독 중지 (※ 이름 명시 필수)
./start.sh down 5017                # 5017 게이트웨이 단독 중지 (※ 이름 명시 필수)
./start.sh restart <name>           # 재시작 (※ 이름 명시 필수)
```

> ⚠️ `down`/`restart`는 인자 없이 호출하면 거부된다 (다른 모델/게이트웨이를 실수로 stop시키는 사고 방지).

상태 확인:

```bash
curl http://localhost:5017/health             # 게이트웨이 health
curl http://localhost:5017/v1/models          # 모델 목록
curl http://localhost:7172/health             # 내부 인스턴스 직접 (디버그용)
```

---

## 🔬 첫 호출 (smoke test)

```bash
# 1초 사인파 생성
python3 -c "
import numpy as np, soundfile as sf
sr=16000; t=np.linspace(0,1,sr,endpoint=False)
sf.write('/tmp/sine_1s.wav', (0.1*np.sin(2*np.pi*440*t)).astype('float32'), sr)
"

# Transcription HTTP
curl http://localhost:5017/v1/audio/transcriptions \
  -F "file=@/tmp/sine_1s.wav" \
  -F "model=Voxtral-Mini-4B-Realtime-2602" \
  -F "language=ko" \
  -F "temperature=0"
# {"text":"","usage":{"type":"duration","seconds":1}}

# Realtime WebSocket — session.created 수신 확인
python3 - <<'PY'
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://localhost:5017/v1/realtime?model=Voxtral-Mini-4B-Realtime-2602") as ws:
        print(json.loads(await ws.recv())["type"])
asyncio.run(main())
PY
# session.created
```

호출 예제·SDK 통합·파라미터 표는 [`../STT_API_GUIDE.md`](../STT_API_GUIDE.md) 참조.

---

## ⚠️ 운영 주의

### 의존성 (Voxtral 필수)

```bash
pip install --user soundfile soxr librosa
```

미설치 시 vLLM 기동 직후 `EngineCore failed to start` + `ImportError: soundfile` 로 fail. 운영계 컨테이너 재배포 시 동일 실패 방지를 위해 `aws/requirements.txt` 추가 권장.

### GPU 점유 충돌 (LLM 인스턴스와 동시 운영)

- **voxtral** 은 GPU 2 단독 → LLM 인스턴스(`vllm/instances/{gemma,qwen}.yaml` GPU 0/1) 와 충돌 없음.
- **qwen3_asr / whisper_v3** 는 GPU 0/1 사용 → LLM 운영 중에는 띄울 수 없음. 한국어 비교 PoC 시 LLM 먼저 stop:

```bash
cd ../vllm && ./start.sh down <인스턴스명>   # 운영 중인 LLM 인스턴스를 하나씩 명시 (예: gemma, qwen)
cd ../stt  && ./start.sh up qwen3_asr        # 또는 whisper_v3
```

### 모델 다운로드

- 첫 실행 시 launcher가 `/models/STT/<HF_ID>/` 로 자동 다운로드.
- Voxtral / Qwen3-ASR / Whisper-large-v3 모두 Apache 2.0 또는 MIT 라이선스 → HF_TOKEN 불필요.
- Voxtral 합계 ~17GB (consolidated.safetensors 8.5GB + model.safetensors 8.5GB).
- 폐쇄망에선 외부망 PC → S3 → `/models/STT/` 사전 동기화. 절차는 [`../STT_OPS_GUIDE.md`](../STT_OPS_GUIDE.md) §8.2.

### 첫 기동 시간 (재기동, 다운로드 제외)

- weight 로딩 ~3초, KV 프로파일링 ~15초, CUDA graph capture ~1초, 게이트웨이 health probe ~10초.
- `./start.sh status`에서 `[UP]`이면 추론 가능.

---

## 🔍 트러블슈팅

상세 표는 [`../STT_OPS_GUIDE.md`](../STT_OPS_GUIDE.md) §10. 자주 보는 항목:

| 증상 | 1차 조치 |
|------|----------|
| `ImportError: soundfile` 로 EngineCore 실패 | `pip install --user soundfile soxr librosa` |
| `[STARTING]` 만 1분 이상 지속 | `tail -f logs/vllm_voxtral.log` 로 cudagraph capture 단계 확인 |
| 게이트웨이 ready 0/1 | 백엔드 voxtral 의 `/health` 가 200인지 확인. 미응답이면 `[STALE]` 정리 후 재기동 |
| GPU OOM | `instances/voxtral.yaml` 의 `gpu_memory_utilization` 0.30~0.35 |
| 5017 → 백엔드 라우팅 안 됨 | `gateways/5017.yaml` 의 `discover_from` 과 인스턴스의 `gateway_port` 일치 확인 |

---

## 📋 다음 단계 (PoC)

- [ ] **한국어 테스트 셋 준비** (Zeroth-Korean / FLEURS Korean / KsponSpeech 일부)
- [ ] **`test_stt.py` 작성** — WER / RTF / latency / 정성 평가 (고유명사·숫자·전문용어)
- [ ] **세 모델 한국어 비교 결과 정리** (`MODEL_STUDY.md` 부록)
- [ ] **동시 N 세션 운영 전환** — `gpu_memory_utilization`/`max_num_seqs`/`max_inflight_requests` 함께 상향
