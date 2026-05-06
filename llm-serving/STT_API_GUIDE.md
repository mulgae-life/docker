# STT API 가이드 (사용자용)

> **대상**: API 사용자 (음성-텍스트 변환을 호출할 개발자)
> **메인 모델**: `Voxtral-Mini-4B-Realtime-2602` (Mistral AI, 13개 다국어 STT)
> **Base URL**: `http://3.38.195.121:5017/v1` (외부)
> **API 호환**: OpenAI Audio API + Realtime API
> **인증**: 불필요 (`Authorization` 헤더 생략 가능)

자체 호스팅한 vLLM 기반 STT 게이트웨이를 **OpenAI Audio API · Realtime API** 그대로 호출하기 위한 가이드입니다.

처음 호출하는 분은 §1~§3만 보면 됩니다. 운영(서버 기동·튜닝·트러블슈팅)은 [`STT_OPS_GUIDE.md`](STT_OPS_GUIDE.md) 참고.

---

## 📑 목차

1. [한눈에 보기](#1-한눈에-보기)
2. [첫 호출](#2-첫-호출)
3. [핵심 기능](#3-핵심-기능)
   - 3.1 Transcriptions (HTTP, OpenAI Audio API)
   - 3.2 Realtime (WebSocket, OpenAI Realtime API)
   - 3.3 언어 / 샘플링 / 타임스탬프
4. [파라미터·응답·에러 레퍼런스](#4-파라미터응답에러-레퍼런스)
5. [클라이언트 통합 예제](#5-클라이언트-통합-예제)

---

## 1. 한눈에 보기

| 항목 | 값 |
|------|-----|
| Base URL | `http://3.38.195.121:5017/v1` |
| 모델명 (`model` 필드) | `Voxtral-Mini-4B-Realtime-2602` |
| API 키 | 불필요 |
| 지원 task | `transcription` (HTTP), `realtime` (WebSocket) |
| 컨텍스트 길이 | `max_model_len: 32768` token (≈ 43분 오디오, 1 token = 80ms) |
| 지원 언어 | 13개 — `ar, de, en, es, fr, hi, it, nl, pt, zh, ja, ko, ru` |
| 권장 샘플링 | `temperature=0.0` (모델카드 강제) |

**엔드포인트 요약**:

| 메서드 | 경로 | 용도 |
|--------|------|------|
| POST | `/v1/audio/transcriptions` | **음성 → 텍스트** (multipart 업로드, 단일 응답) |
| POST | `/v1/audio/translations` | 음성 → 영어 텍스트 |
| WS   | `/v1/realtime` | **실시간 스트리밍 STT** (양방향 WebSocket) |
| GET  | `/v1/models` | 로드된 모델 목록 |
| GET  | `/health` | 게이트웨이 헬스체크 |

> 게이트웨이 `/health`는 ready 백엔드 1개 이상이면 200, 없으면 503. JSON 본문에 `{"status": "ok", "ready": N, "total": N}`.

---

## 2. 첫 호출

> 호출 전 살아있는지 확인: `curl http://3.38.195.121:5017/health` → `200 OK`.

### 2.1 Transcriptions (HTTP, multipart) — curl

```bash
curl http://3.38.195.121:5017/v1/audio/transcriptions \
  -F "file=@sample_ko.wav" \
  -F "model=Voxtral-Mini-4B-Realtime-2602" \
  -F "language=ko" \
  -F "temperature=0"
```

응답:

```json
{
  "text": "안녕하세요, 오늘 날씨가 좋네요.",
  "usage": {"type": "duration", "seconds": 4}
}
```

### 2.2 Transcriptions — Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://3.38.195.121:5017/v1",
    api_key="not-needed",  # vLLM 기본 인증 없음. 빈 문자열은 SDK가 거부.
)

with open("sample_ko.wav", "rb") as f:
    resp = client.audio.transcriptions.create(
        file=f,
        model="Voxtral-Mini-4B-Realtime-2602",
        language="ko",
        temperature=0,
    )
print(resp.text)
```

### 2.3 Realtime (WebSocket) — Python

```python
import asyncio, json, websockets

URL = "ws://3.38.195.121:5017/v1/realtime?model=Voxtral-Mini-4B-Realtime-2602"

async def main():
    async with websockets.connect(URL) as ws:
        # 첫 이벤트: session.created
        msg = json.loads(await ws.recv())
        print("[server]", msg["type"])

        # session.update — 입력 오디오 포맷/언어 등 협상
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"language": "ko"},
            },
        }))

        # input_audio_buffer.append — 오디오 chunk를 base64로 push
        # (PCM16 16kHz 모노 권장. 80ms 단위 chunk가 모델카드 권장 sweet spot)
        # ... 클라이언트의 마이크/파일을 base64 인코딩해 반복 send ...
asyncio.run(main())
```

> 자세한 Realtime 프로토콜은 OpenAI 공식 문서: <https://platform.openai.com/docs/api-reference/realtime>. vLLM 0.19.1의 `session.created` 페이로드는 OpenAI 표준의 부분집합(`type, id, created`)이며, 클라이언트가 `session.update` / `input_audio_buffer.append` / `response.create` 등을 보내야 후속 이벤트가 흐릅니다.

---

## 3. 핵심 기능

### 3.1 Transcriptions (HTTP)

OpenAI Audio API와 동일. multipart/form-data로 audio 파일 + form 필드 업로드.

| 필드 | 필수 | 값 | 설명 |
|------|:----:|----|------|
| `file` | O | 파일 | wav/mp3/flac/m4a 등 (vLLM이 librosa로 디코드) |
| `model` | O | `Voxtral-Mini-4B-Realtime-2602` | `/v1/models`로 확인한 정확한 ID |
| `language` | 권장 | `ko` 등 ISO-639-1 | 지정 시 언어 자동감지 비용 절약 |
| `temperature` | 권장 | `0` | 모델카드 권장 (수치 정확도/숫자/고유명사 안정) |
| `response_format` | 선택 | `json`(기본) / `text` | **Voxtral-Realtime은 `verbose_json` 미지원** (요청 시 400). Whisper/Qwen3-ASR 등 일반 transcription 모델은 `verbose_json` 지원 — 모델 별로 확인 필요 |
| `timestamp_granularities[]` | 선택 | `word` / `segment` | `verbose_json` 일 때만 의미 있음. Voxtral-Realtime은 미지원 |

게이트웨이 timeout은 600초 — 1시간 미만 오디오는 보통 안전.

### 3.2 Realtime (WebSocket)

OpenAI Realtime API와 동일한 WebSocket 프로토콜. 게이트웨이가 양방향 frame을 그대로 백엔드 vLLM `/v1/realtime`에 relay합니다.

권장 입력 포맷:

| 항목 | 권장값 | 비고 |
|------|--------|------|
| 샘플레이트 | 16,000 Hz | Voxtral 학습 기본 |
| 채널 | mono (1 channel) | 스테레오는 다운믹스 후 전송 |
| 인코딩 | PCM16 (little-endian) | base64로 `input_audio_buffer.append`에 첨부 |
| chunk size | 80ms × N (모델카드 권장 480ms) | 짧을수록 latency↓, 정확도↓ |

**WebSocket close code 매핑** (게이트웨이 추가):

| code | 의미 |
|------|------|
| 4429 | 과부하 차단 — `Retry-After` 후 재시도 |
| 4503 | 사용 가능한 백엔드 없음 |
| 4500 | 게이트웨이 → 백엔드 연결 실패 |
| 1000 | 정상 종료 |

### 3.3 언어 / 샘플링 / 타임스탬프

- **언어 자동 감지 vs 명시**: `language=ko` 명시 시 정확도/속도 모두 유리. 다국어 혼합 음성은 `language` 생략 → 자동 감지.
- **temperature**: 0이 권장. 0이 아니면 같은 입력에 다른 출력이 나올 수 있고, 숫자/고유명사 안정성↓.
- **타임스탬프**: `response_format=verbose_json` + `timestamp_granularities[]=word` 조합으로 `words[].start/end` 반환 — **단 Voxtral-Realtime은 미지원**. word/segment 단위 타임스탬프가 필수면 Whisper-large-v3 또는 Qwen3-ASR 인스턴스(`stt/instances/{whisper_v3,qwen3_asr}.yaml`, 직접 :7170/:7171 노출) 사용.

---

## 4. 파라미터·응답·에러 레퍼런스

### 4.1 응답 (transcriptions, json)

```json
{
  "text": "변환된 텍스트",
  "usage": {"type": "duration", "seconds": 4}
}
```

### 4.2 응답 (transcriptions, verbose_json) — Voxtral-Realtime 미지원

> ⚠️ **Voxtral-Mini-4B-Realtime-2602 는 `response_format=verbose_json` 을 지원하지 않습니다** (400 BadRequestError). 아래 형식은 Whisper-large-v3 / Qwen3-ASR 등 일반 transcription 모델에서 반환되는 OpenAI 호환 형태이며, 모델별 지원 여부는 `/v1/models` 응답 또는 운영자 확인 필요.

```json
{
  "task": "transcribe",
  "language": "ko",
  "duration": 4.0,
  "text": "...",
  "segments": [
    {"id": 0, "start": 0.0, "end": 1.5, "text": "...",
     "tokens": [...], "temperature": 0.0,
     "avg_logprob": -0.2, "compression_ratio": 1.1, "no_speech_prob": 0.01}
  ]
}
```

### 4.3 에러 (HTTP)

| 상태 | 의미 | 클라이언트 대응 |
|------|------|-----------------|
| 400 | 잘못된 multipart 또는 form 필드 | 요청 본문 확인 |
| 429 | 과부하 차단 (`max_inflight` 또는 `max_queue` 초과) | `Retry-After` 헤더 후 재시도 |
| 502 | 게이트웨이 → 백엔드 연결 실패 | 잠시 후 재시도, 운영팀 통보 |
| 503 | 사용 가능한 백엔드 없음 (재기동 중 등) | 잠시 후 재시도 |
| 504 | 백엔드 타임아웃 (600초 초과) | 오디오를 더 짧게 분할 |

### 4.4 에러 본문 형식

```json
{"error": {"message": "...", "type": "rate_limit_error|server_error|timeout|bad_gateway", "code": "..."}}
```

---

## 5. 클라이언트 통합 예제

### 5.1 .env 설정 (chatbot/RAG 등에서 STT 모듈)

```bash
# 외부에서 호출
STT_BASE_URL=http://3.38.195.121:5017/v1
STT_MODEL=Voxtral-Mini-4B-Realtime-2602
STT_LANGUAGE=ko

# 동일 EC2 안에서 호출 (게이트웨이 같은 호스트)
STT_BASE_URL=http://localhost:5017/v1
```

### 5.2 FastAPI에서 transcription 프록시

```python
import os
from openai import AsyncOpenAI
from fastapi import FastAPI, UploadFile

app = FastAPI()
stt = AsyncOpenAI(base_url=os.environ["STT_BASE_URL"], api_key="not-needed")

@app.post("/transcribe")
async def transcribe(file: UploadFile):
    data = await file.read()
    resp = await stt.audio.transcriptions.create(
        file=(file.filename, data, file.content_type or "audio/wav"),
        model=os.environ["STT_MODEL"],
        language=os.environ.get("STT_LANGUAGE", "ko"),
        temperature=0,
    )
    return {"text": resp.text}
```

### 5.3 LangChain (audio agent의 한 노드)

```python
from langchain_core.tools import tool
from openai import OpenAI

_stt = OpenAI(base_url="http://3.38.195.121:5017/v1", api_key="not-needed")

@tool
def transcribe_audio(path: str, language: str = "ko") -> str:
    """오디오 파일 경로를 받아 한국어 텍스트로 변환."""
    with open(path, "rb") as f:
        return _stt.audio.transcriptions.create(
            file=f, model="Voxtral-Mini-4B-Realtime-2602",
            language=language, temperature=0,
        ).text
```

---

## 다음 단계

- 운영자 관점(서버 기동·튜닝·트러블슈팅): [`STT_OPS_GUIDE.md`](STT_OPS_GUIDE.md)
- 배포 절차(로컬 → S3 → 운영계): [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)
- 후보 모델 비교 / 시나리오 분석: [`stt/MODEL_STUDY.md`](stt/MODEL_STUDY.md)
