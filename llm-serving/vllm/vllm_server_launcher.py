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
import json
import logging
import os
import signal
import socket
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
INSTANCES_DIR = os.path.join(BASE_DIR, "instances")
RUNTIME_DIR = os.path.join(INSTANCES_DIR, ".runtime")

# vllm serve --config에 전달하지 않는 런처 전용 키 + 게이트웨이 매칭용 메타 키.
# gateway_port: gateways/<port>.yaml의 discover_from이 인스턴스 yaml에서 읽는 메타.
#   vllm serve는 이 키를 알지 못하므로 임시 config에서 제거해야 한다.
# port: 자동 회피 로직이 결정한 실제 포트를 --port CLI로 따로 넘기므로 yaml에서는 제거.
_LAUNCHER_KEYS = {"gpus", "download_dir", "gateway_port", "port"}


def parse_args():
    p = argparse.ArgumentParser(
        description="vLLM 서버 런처",
        epilog="위 옵션 외 모든 인자는 vllm serve에 그대로 전달됩니다.",
    )
    p.add_argument(
        "-c", "--config",
        required=True,
        help="인스턴스 설정 파일 경로 (예: instances/gemma.yaml)",
    )
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


def _is_port_free(port: int, host: str = "0.0.0.0") -> bool:
    """포트가 비어있는지 socket binding test로 확인.

    SO_REUSEADDR을 켜고 잠깐 binding을 시도한다. 실제 binding은 vllm serve가
    수행하므로 검사와 vLLM 기동 사이에 다른 프로세스가 잡을 수 있는 race
    window가 있지만, 운영 환경(단일 사용자)에서는 실용적으로 무시 가능.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError:
        return False
    finally:
        s.close()
    return True


def _find_free_port(start: int, max_tries: int = 100) -> int:
    """yaml의 port를 시작점으로 비어있는 첫 포트를 찾는다.

    동작:
        - start 포트가 비어있으면 그대로 사용 (의도된 포트 보존).
        - 사용 중이면 +1, +2 ... max_tries까지 회피 탐색.
        - 모두 사용 중이면 RuntimeError로 fail-fast.

    의도: 인스턴스 yaml을 복붙해 같은 port가 남아있는 경우에도 자동 회피하여
    같은 게이트웨이 아래 LB 인스턴스를 올릴 수 있게 한다. 게이트웨이는
    runtime 파일에서 실제 포트를 읽으므로 backends 등록은 자동.
    """
    for offset in range(max_tries):
        port = start + offset
        if _is_port_free(port):
            if offset > 0:
                logger.info(
                    "port %d 사용 중 → +%d 회피하여 %d 사용", start, offset, port,
                )
            return port
    raise RuntimeError(
        f"비어있는 port를 찾지 못함: {start} ~ {start + max_tries - 1} 모두 사용 중",
    )


def _instance_name_from_config(config_path: str) -> str:
    """yaml 파일 경로에서 인스턴스 이름 추출 (예: instances/gemma.yaml → 'gemma')."""
    return os.path.splitext(os.path.basename(config_path))[0]


def _runtime_path(name: str) -> str:
    """인스턴스 이름에 대응하는 runtime json 경로."""
    return os.path.join(RUNTIME_DIR, f"{name}.json")


def _write_runtime_file(name: str, port: int, yaml_port_hint: int, model: str) -> str:
    """launcher가 실제 사용 중인 port를 runtime 파일에 기록한다.

    형식:
        {"port": <actual>, "yaml_port_hint": <yaml에 적힌 시작 포트>,
         "pid": <launcher PID>, "model": <모델명>, "started_at": <epoch>}

    게이트웨이의 _discover_backends가 이 파일을 우선 참조하여 backends에
    실제 포트를 등록한다. 파일이 없으면 yaml의 port로 fallback.
    """
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    path = _runtime_path(name)
    data = {
        "port": port,
        "yaml_port_hint": yaml_port_hint,
        "pid": os.getpid(),
        "model": model,
        "started_at": time.time(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("runtime 기록: %s (port=%d, pid=%d)", path, port, os.getpid())
    return path


def _remove_runtime_file(name: str) -> None:
    """launcher 종료 시 자기 runtime 파일을 정리한다."""
    path = _runtime_path(name)
    try:
        os.unlink(path)
        logger.info("runtime 정리: %s", path)
    except OSError:
        pass


def _is_pid_alive(pid: int) -> bool:
    """PID가 살아있는지 signal 0으로 검사 (실제 신호는 안 보냄)."""
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _cleanup_stale_runtime_files() -> None:
    """이전 실행에서 SIGKILL/crash로 남은 runtime 파일을 정리한다.

    PID가 살아있지 않은 항목만 삭제하여 동시 실행 중인 다른 인스턴스의
    runtime 파일은 보존한다.
    """
    if not os.path.isdir(RUNTIME_DIR):
        return
    for path in glob.glob(os.path.join(RUNTIME_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("pid")
            if pid is None or not _is_pid_alive(pid):
                os.unlink(path)
                logger.info("stale runtime 정리: %s (pid=%s 죽음)", path, pid)
        except (OSError, json.JSONDecodeError):
            try:
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

    # 이전 실행에서 SIGKILL/crash로 남은 임시 config / runtime 파일 정리.
    _cleanup_stale_configs()
    _cleanup_stale_runtime_files()

    # ── 설정 로드 ──
    config_path = os.path.abspath(args.config)
    if not os.path.exists(config_path):
        logger.error("설정 파일 없음: %s", config_path)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    instance_name = _instance_name_from_config(config_path)

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

    # ── 포트 자동 회피 ──
    # yaml의 port는 hint. 사용 중이면 +1 회피하여 비어있는 첫 포트 사용.
    # 결정된 실제 포트는 runtime 파일에 기록 → 게이트웨이가 우선 참조.
    yaml_port = config.get("port")
    if yaml_port is None:
        logger.error("yaml에 port 키가 없습니다 (port hint 필요): %s", config_path)
        sys.exit(1)
    actual_port = _find_free_port(yaml_port)

    runtime_path = _write_runtime_file(
        name=instance_name,
        port=actual_port,
        yaml_port_hint=yaml_port,
        model=config.get("model", ""),
    )

    # ── vllm serve 명령 구성 ──
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    runtime_config = _write_vllm_config(config)  # _LAUNCHER_KEYS(port 포함) 제거됨

    cmd = ["vllm", "serve"]
    if model:
        cmd.append(model)
    cmd.extend(["--config", runtime_config])

    # 실제 포트는 yaml에서 제거되었으므로 CLI로 명시 전달 (자동 회피 결과 반영).
    cmd.extend(["--port", str(actual_port)])

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
        # runtime 파일은 종료 시 정리 (게이트웨이가 더는 이 인스턴스를 보지 않게)
        _remove_runtime_file(instance_name)
    sys.exit(proc.returncode if proc else 1)


if __name__ == "__main__":
    main()
