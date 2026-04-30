#!/usr/bin/env python3
"""vLLM 서버 런처

vllm serve 위에 환경변수 관리 + 모델 자동 다운로드를 추가한 래퍼.
start.sh에서 호출되며, 직접 실행도 가능.

설정 모델:
    -c instances/<name>.yaml 형태로 인스턴스 단위 yaml을 받는다.
    yaml 안의 메타 키(gateway_port, gpus 등)는 _LAUNCHER_KEYS로 필터링되어
    vllm serve에는 전달되지 않는다.

사용법:
    # start.sh를 통한 실행 (권장)
    ./start.sh up                       # 전체 인스턴스 + 게이트웨이 기동
    ./start.sh up gemma                 # 단일 인스턴스 (instances/gemma.yaml)

    # 직접 실행
    python vllm_server_launcher.py -c instances/gemma.yaml
    python vllm_server_launcher.py -c instances/qwen.yaml

    # 모델 다운로드만
    python vllm_server_launcher.py -c instances/qwen.yaml --download-only

    # Gated 모델
    HF_TOKEN=hf_xxx python vllm_server_launcher.py -c instances/<name>.yaml
"""
import argparse
import glob
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("vllm-launcher")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(BASE_DIR, "vllm_config.yaml")

# vllm serve --config에 전달하지 않는 런처 전용 키 + 게이트웨이 매칭용 메타 키.
# gateway_port: gateways/<port>.yaml의 discover_from이 인스턴스 yaml에서 읽는 메타.
#   vllm serve는 이 키를 알지 못하므로 임시 config에서 제거해야 한다.
_LAUNCHER_KEYS = {"gpus", "download_dir", "gateway_port"}


def parse_args():
    p = argparse.ArgumentParser(
        description="vLLM 서버 런처",
        epilog="위 옵션 외 모든 인자는 vllm serve에 그대로 전달됩니다.",
    )
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="설정 파일 경로")
    p.add_argument("-g", "--gpu", type=str, help="CUDA_VISIBLE_DEVICES (예: 0 또는 0,1)")
    p.add_argument("-m", "--model", type=str, help="HF 모델 ID (config override)")
    p.add_argument("--online", action="store_true", help="HF 온라인 모드 허용")
    p.add_argument("--download-only", action="store_true", help="모델 다운로드만 수행")
    args, extra = p.parse_known_args()
    return args, extra


def download_model(model_id: str, local_dir: str) -> None:
    """HuggingFace 모델을 snapshot_download API로 다운로드."""
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    token = os.environ.get("HF_TOKEN") or None

    logger.info("모델 다운로드 시작: %s → %s", model_id, local_dir)
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=model_id, local_dir=local_dir, token=token)
    except Exception as e:
        logger.error(
            "모델 다운로드 실패: %s\n"
            "Gated 모델이면 HF_TOKEN 환경변수를 설정하세요:\n"
            "  export HF_TOKEN=hf_xxx",
            e,
        )
        sys.exit(1)
    logger.info("모델 다운로드 완료: %s", local_dir)


def _write_vllm_config(config: dict) -> str:
    """런처 전용 키를 제거한 vllm serve용 임시 config 파일을 생성한다."""
    vllm_only = {k: v for k, v in config.items() if k not in _LAUNCHER_KEYS}
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix=".vllm_serve_", dir=BASE_DIR)
    with os.fdopen(fd, "w") as f:
        yaml.dump(vllm_only, f, default_flow_style=False, allow_unicode=True)
    return path


def _cleanup_stale_configs(min_age_seconds: int = 60) -> None:
    """이전 실행에서 남은 임시 config 파일을 정리한다.

    SIGKILL/crash로 finally가 실행되지 않은 경우를 대비한 보험.
    min_age_seconds 이상 오래된 파일만 삭제하여 동시 기동 중인 다른 런처의
    임시 파일을 지우지 않도록 방어한다.
    """
    pattern = os.path.join(BASE_DIR, ".vllm_serve_*.yaml")
    now = time.time()
    for path in glob.glob(pattern):
        try:
            if now - os.path.getmtime(path) >= min_age_seconds:
                os.unlink(path)
        except OSError:
            pass


def _raise_keyboard_interrupt(signum, frame):
    """SIGTERM을 KeyboardInterrupt로 승격하여 main의 finally cleanup이 실행되게 한다."""
    raise KeyboardInterrupt()


def main():
    args, passthrough = parse_args()

    # start.sh의 stop/restart는 kill(SIGTERM)을 사용하므로 핸들러 등록 필요.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    # 이전 실행에서 SIGKILL/crash로 남은 임시 config 정리.
    _cleanup_stale_configs()

    # ── 설정 로드 ──
    config_path = os.path.abspath(args.config)
    if not os.path.exists(config_path):
        logger.error("설정 파일 없음: %s", config_path)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # ── 환경변수 ──
    env = os.environ.copy()
    if args.gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = args.gpu
        logger.info("CUDA_VISIBLE_DEVICES=%s", args.gpu)
    if not args.online:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        logger.info("HF 오프라인 모드")

    # ── 모델 경로 해석 ──
    model = args.model or config.get("model", "")
    download_dir = config.get("download_dir", "")

    if model and not os.path.isabs(model) and download_dir:
        local_path = os.path.join(download_dir, model)
        if os.path.isdir(local_path):
            logger.info("모델 경로 해석: %s → %s", model, local_path)
        else:
            logger.info("로컬 모델 없음, 자동 다운로드: %s", local_path)
            download_model(model, local_path)
        model = local_path
    elif model and os.path.isabs(model) and not os.path.isdir(model):
        logger.error(
            "모델 경로가 존재하지 않습니다: %s\n"
            "config에서 HF 모델 ID 형식(예: google/gemma-4-31B-it)을 사용하세요.",
            model,
        )
        sys.exit(1)

    if args.download_only:
        logger.info("다운로드 완료. 서버를 실행하지 않습니다.")
        sys.exit(0)

    # ── vllm serve 명령 구성 ──
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    runtime_config = _write_vllm_config(config)

    cmd = ["vllm", "serve"]
    if model:
        cmd.append(model)
    cmd.extend(["--config", runtime_config])

    # served_model_name 자동 추출 (config에 미설정 시)
    original_id = args.model or config.get("model", "")
    if "served_model_name" not in config and "/" in original_id:
        derived_name = original_id.split("/")[-1]
        cmd.extend(["--served-model-name", derived_name])
        logger.info("served_model_name 자동 추출: %s", derived_name)

    # vLLM YAML 파서(argparse_utils.py:501-504)는 bool true만 --key로 변환하고
    # bool false는 그냥 버린다. async_scheduling은 기본값이 None이고,
    # None으로 들어가면 vllm/config/vllm.py:755-788의 자동 활성화 로직이
    # True로 덮어쓴다(멀티모달 encoder cache race 유발).
    # → YAML에서 false로 명시한 의도를 관철시키려면 --no-async-scheduling
    #   플래그를 직접 추가해야 한다(argparse BooleanOptionalAction 덕분에 유효).
    # vLLM이 bool false 전달 버그를 고치면 이 블록은 제거 가능.
    if config.get("async_scheduling") is False:
        cmd.append("--no-async-scheduling")
        logger.info("async_scheduling: false → --no-async-scheduling 플래그 추가")

    cmd.extend(passthrough)
    logger.info("실행: %s", " ".join(cmd))

    # ── 프로세스 실행 ──
    proc = None
    try:
        proc = subprocess.Popen(cmd, env=env)
        proc.wait()
    except KeyboardInterrupt:
        logger.info("서버 종료 중...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("정상 종료 실패, 강제 종료")
            proc.kill()
    except FileNotFoundError:
        logger.error(
            "'vllm' 명령어를 찾을 수 없습니다. "
            "pip install vllm 으로 설치했는지 확인하세요."
        )
        sys.exit(1)
    finally:
        try:
            os.unlink(runtime_config)
        except OSError:
            pass
    sys.exit(proc.returncode if proc else 1)


if __name__ == "__main__":
    main()
