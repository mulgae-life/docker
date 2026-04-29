# vLLM 버그/픽스 리포트 — 2026-04-18 ~ 2026-04-19

> **대상 서버**: `vllm_gpu0_1` (Qwen3.6-35B-A3B-FP8, TP=2, vLLM v0.19.0)
> **커밋**: `78f007f` — 멀티모달 encoder cache race 방어 + QA 테스트 보강

---

## ✅ 해결된 이슈 (3건)

### 1️⃣ `AssertionError: Encoder cache miss` (멀티모달 race)

| 항목 | 내용 |
|------|------|
| **증상** | 동시 이미지 요청 처리 중 encoder 해시 조회 실패로 엔진 크래시 |
| **근본 원인** | `async_scheduling: true`(기본값)일 때 encoder 캐시 invalidation과 다음 스텝 스케줄링이 경합 |
| **수정** | `vllm_config.yaml`에 `async_scheduling: false` + `max_num_seqs` 상한(encoder_cache_size / 이미지 토큰) |

### 2️⃣ `AssertionError: Chunked MM input is required` (재기동 직후)

| 항목 | 내용 |
|------|------|
| **증상** | 1️⃣ 해결 시도로 `disable_chunked_mm_input: true`를 추가하자 엔진이 기동조차 못 함 |
| **근본 원인** | Qwen3.6은 Mamba-hybrid 구조 → prefix caching이 자동으로 `mamba_cache_mode='align'` 활성 → `validate_block_size()`(`vllm/config/vllm.py:1730`)가 `disable_chunked_mm_input=True`를 거부 |
| **수정** | YAML에서 해당 옵션 제거 + 설명 주석 추가, `VLLM_OPS_GUIDE.md`에 "설정 금지" 경고 |
| **남은 방어선** | `async_scheduling: false` + `max_num_seqs ≤ (encoder_cache_size / 이미지 토큰)` — 이 두 개로 충분 |

### 3️⃣ `async_scheduling: false`가 무시되던 문제

| 항목 | 내용 |
|------|------|
| **증상** | YAML에 `false`로 적었는데 기동 로그에 `Asynchronous scheduling is enabled.` 출력 |
| **근본 원인** | vLLM YAML 파서(`vllm/utils/argparse_utils.py:501-504`)가 **bool `true`만** `--key` 플래그로 변환. `false`는 drop → `async_scheduling`이 `None`으로 진입 → `vllm/config/vllm.py:755-788`의 auto-enable 로직이 `True`로 덮어씀 |
| **수정** | `vllm_server_launcher.py`에서 `config["async_scheduling"] is False`이면 `--no-async-scheduling` 플래그를 CLI에 직접 주입. argparse `BooleanOptionalAction`이 이를 수용 |
| **검증** | 재기동 로그에서 `Asynchronous scheduling is disabled.` 및 non-default args에 `async_scheduling: False` 확인 |

### 📎 부가 작업

- `test_vllm_server.py` 멀티모달 테스트 4종 추가
  - 9.1: 단일 이미지 강아지 수 질의 (정답 "5" 또는 "다섯" 검증)
  - 9.2: 동시 5개 이미지 요청 (max_num_seqs 상한)
  - 9.3: 동시 10개 이미지 요청 (큐잉)
  - 9.4: 이미지 5 + 텍스트 5 혼합 동시 요청
- 테스트 리소스 `image.png`(강아지 5마리) 추가
- base64 data URL 로더 `_load_test_image()` 및 OpenAI 포맷 `_chat_with_image()` 헬퍼

---

## ✅ 해결된 이슈 · 하드웨어 장애 (2026-04-20 AWS stop/start로 복구)

### 4️⃣ GPU1 PCIe fallen off the bus — 발생 2026-04-19 15:49:01, 복구 2026-04-20

| 항목 | 내용 |
|------|------|
| **1차 증상** (`vllm_gpu0_1.log`) | `torch.AcceleratorError: CUDA error: unknown error` in `_zero_block_ids` → `vllm/v1/worker/utils.py:208 idx.copy_(..., non_blocking=True)` → EngineCore `TimeoutError: RPC call to execute_model timed out` → `EngineDeadError` → APIServer shutdown |
| **2차 증상** (`vllm_gpu2_3.log`) | 우회 시도(GPU2/3으로 이관)도 실패. vLLM import 단계에서 `pynvml.NVMLError_Unknown` — `vllm/platforms/cuda.py:613 log_warnings()`가 시스템 전체 NVML 장치를 순회하다 GPU1 핸들 조회에서 실패 |
| **근본 원인** | GPU1(`0000:3a:00.0`)이 PCIe 버스에서 탈락. 증거:<br>• `nvidia-smi -i 1` → `Unable to determine the device handle ... Unknown Error`<br>• `/sys/bus/pci/devices/0000:3a:00.0/current_link_speed = Unknown`<br>• `current_link_width = 63` (비정상) |
| **vLLM 코드 이슈 아님** | `cudaErrorUnknown`은 NVIDIA 문서상 "An unknown internal error". vLLM 로그 자체도 `CUDA kernel errors might be asynchronously reported at some other API call`로 명시 — 최초 감지 지점일 뿐 원인 지점 아님 |
| **회피 한계** | `CUDA_VISIBLE_DEVICES=2,3`으로도 vLLM 기동 불가. `CUDA_VISIBLE_DEVICES`는 CUDA runtime 필터이고, vLLM의 `log_warnings`는 NVML을 직접 호출하기 때문 |
| **좀비 프로세스** | vLLM APIServer(pid 2413958)가 GPU1 메모리 ~2.5GB를 쥔 채 반쯤 살아있음 (engine core만 사망) |

#### 🎯 복구 결과 (2026-04-20, AWS EC2 stop/start)

| 항목 | 장애 중 | 복구 후 |
|------|---------|---------|
| GPU1 `nvidia-smi` 응답 | `Unable to determine handle` | ✅ 정상 |
| GPU1 `current_link_speed` | `Unknown` | ✅ `16.0 GT/s PCIe` |
| GPU1 UUID | `e2c04e22-3854-4ce8-6940-01663a388466` | 🔄 `db7f1201-7f52-203b-d3d1-1f16c1386d59` |
| GPU1 ECC uncorrectable | (조회 불가) | ✅ `0` |

**핵심**: GPU1 UUID가 바뀐 점이 결정적. AWS EC2 `stop → start`는 **다른 물리 호스트로 인스턴스를 재배정**한다. 즉 이번 장애는 결함 있는 물리 GPU 카드 자체의 문제였고, AWS가 해당 호스트에서 빼내면서 자연히 해결됨. AWS 내부 RMA 처리 대상.

#### 🧭 복구 방법 비교 (향후 동일 증상 참고)

| 방법 | 효과 | 비고 |
|------|------|------|
| `reboot` | 같은 물리 호스트 유지 | PCIe 링크 재협상만 시도, 물리 결함이면 재발 |
| **AWS `stop → start`** | ✅ **다른 물리 호스트로 이전** | UUID 변경으로 확인. 이번 사례 복구 방법 |
| PCI hot-remove | root 권한 필요, L40S 급은 대부분 실패 | 시도하지 않음 |

#### 📋 향후 동일 증상 감지 시 체크리스트

```
1) nvidia-smi -L                                              # GPU 4장 다 보이나
2) cat /sys/bus/pci/devices/0000:XX:00.0/current_link_speed   # "Unknown"이면 PCIe drop
3) AWS EC2 콘솔에서 stop → start (재부팅 X)
4) 복구 후 UUID 비교로 호스트 재배정 확인
5) dmesg | grep -iE "xid|nvrm" 로 Xid 코드 수집 (결함 성격 판단)
```

---

## 🗂️ 변경 파일 (commit `78f007f`, +355 / −11)

| 파일 | 변경 요약 |
|------|----------|
| `vllm_config.yaml` | `disable_chunked_mm_input` 제거 + Mamba-hybrid 금지 사유 주석 |
| `vllm_server_launcher.py` | `--no-async-scheduling` 자동 주입 로직 추가 |
| `VLLM_OPS_GUIDE.md` | YAML bool false 제약·Mamba 호환성 경고 추가 |
| `test_vllm_server.py` | 멀티모달 테스트 4종 + base64 data URL 로더 |
| `image.png` | 테스트 리소스 (강아지 5마리, 353KB) |

---

## 📚 참조한 vLLM 소스 위치 (읽기 전용 분석)

| 파일 | 줄 | 내용 |
|------|----|------|
| `vllm/config/vllm.py` | 1715-1754 | `validate_block_size()` — `mamba_cache_mode='align'` 시 `disable_chunked_mm_input=True` 금지 |
| `vllm/config/vllm.py` | 725-793 | `async_scheduling` auto-enable 로직 (None → True) |
| `vllm/utils/argparse_utils.py` | 454-519 | YAML 파서 bool false drop 버그 (501-504) |
| `vllm/engine/arg_utils.py` | 1250-1263 | `async_scheduling`의 `BooleanOptionalAction` 등록 |
| `vllm/config/scheduler.py` | 90-120 | `SchedulerConfig` 필드 정의 |
| `vllm/v1/worker/utils.py` | 208 | 크래시 최초 감지 위치 (`idx.copy_`) |
| `vllm/platforms/cuda.py` | 605-613 | `log_warnings` NVML 전체 장치 순회 |

---

## 🧭 교훈

- **YAML bool false 주의**: vLLM YAML 파서는 `key: false`를 drop한다. 기본값이 `None`이고 auto-enable 로직을 타는 bool 필드(`async_scheduling` 등)는 launcher에서 `--no-key`를 직접 주입해야 한다.
- **Mamba-hybrid 모델 제약**: prefix caching을 쓰면 `mamba_cache_mode='align'`이 강제되어 attention block_size가 확장되므로, MM 입력 chunking을 끌 수 없다. encoder cache race 방어는 `async_scheduling: false` + `max_num_seqs` 상한 조합으로만 가능.
- **`CUDA error: unknown` 디버깅 원칙**: vLLM 스택트레이스는 최초 감지 지점을 보여줄 뿐. GPU 하드웨어(PCIe link, NVML 핸들, Xid 로그)를 먼저 확인해야 코드 회피로 시간 낭비를 막는다.
- **AWS EC2 GPU fallen off bus 복구**: `reboot`가 아닌 **`stop → start`**가 정답. reboot는 같은 물리 호스트에 머무르지만 stop/start는 다른 호스트로 재배정되어 결함 카드를 떠난다. 복구 확인은 GPU UUID 변경 여부로 판별.
