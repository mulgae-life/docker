# vLLM Speed Test Results

동시성/입력/출력 매트릭스 기준 속도 측정 누적 결과. 실행할 때마다 행이 추가됩니다.

**컬럼**
- `TTFT_ms`: 첫 토큰까지 지연 (ms, prefill 성능)
- `TPS`: 요청당 출력 토큰 생성 속도 (output tok/s, decode 성능 = 텍스트 출력 속도)
- `ok/N`: 성공 요청 / 전체 요청 (실패 섞이면 TPS가 왜곡되니 확인용)

| timestamp | model | concurrency | input | max_tok | ok/N | TTFT_ms | TPS |
|---|---|---|---|---|---|---|---|
| 2026-05-13 13:36:56 | gemma-4-31B-it | 1 | short | 400 | 5/5 | 81.8 | 64.8 |
