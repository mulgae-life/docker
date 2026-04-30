#!/usr/bin/env python3
"""vLLM Gateway — 로드밸런싱 + 헬스체크 + 웜업 리버스 프록시.

다수의 vLLM 인스턴스 앞단에서 Least-Connection 로드밸런싱,
주기적 헬스체크, 기동/재기동 시 자동 웜업을 제공한다.

구조 (격리 페어 + 자동 디스커버리):
    클라이언트 → Gateway(:5015) → vLLM(:7070)            [Gemma 페어]
    클라이언트 → Gateway(:5016) → vLLM(:7071)            [Qwen 페어]
    (LB 시) Gateway(:5015) → vLLM(:7070, :7072, ...)     [동일 게이트웨이 소속]

자동 디스커버리:
    gateways/<port>.yaml 의 discover_from 디렉토리(예: ../instances)를 스캔하여
    각 인스턴스 yaml의 gateway_port == 자기 게이트웨이 포트인 것만
    backends로 등록한다. 인스턴스를 추가하려면 instances/*.yaml에 한 파일을
    추가하고 게이트웨이만 재기동하면 된다.

사용법:
    python vllm_gateway.py -c gateways/5015.yaml
    python vllm_gateway.py -c gateways/5016.yaml

    # 백그라운드 실행
    mkdir -p logs && nohup python vllm_gateway.py -c gateways/5015.yaml \
        > logs/gateway_5015.log 2>&1 &
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# ═══════════════════════════════════════════════════════
# 로깅
# ═══════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("vllm-gateway")

# 파일 로깅: 삭제해도 자동 재생성
_file_handler = logging.handlers.WatchedFileHandler(
    os.path.join(LOG_DIR, "gateway.log"), encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logging.getLogger().addHandler(_file_handler)


# ═══════════════════════════════════════════════════════
# Pydantic 설정 모델
# ═══════════════════════════════════════════════════════


class BackendConfig(BaseModel):
    """해석된 백엔드 설정. load_config에서 포트가 자동 할당된다."""

    host: str = "127.0.0.1"
    port: int

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class GatewayServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 5015
    log_level: str = "info"


class HealthCheckConfig(BaseModel):
    interval_seconds: int = 10
    timeout_seconds: int = 3
    unhealthy_threshold: int = 3
    healthy_threshold: int = 1


class BootPollConfig(BaseModel):
    interval_seconds: int = 5
    timeout_seconds: int = 300


class InferenceWarmupConfig(BaseModel):
    prompt: str = "안녕하세요"
    max_tokens: int = 5
    timeout_seconds: int = 60


class WarmupConfig(BaseModel):
    enabled: bool = True
    boot_poll: BootPollConfig = BootPollConfig()
    inference: InferenceWarmupConfig = InferenceWarmupConfig()


class PrefixCacheWarmupConfig(BaseModel):
    enabled: bool = True
    max_tokens: int = 1
    timeout_seconds: int = 60
    system_prompt: str = ""


class HttpClientConfig(BaseModel):
    timeout_seconds: int = 300
    connect_timeout_seconds: int = 5
    max_connections: int = 100


class GatewayConfig(BaseModel):
    gateway: GatewayServerConfig = GatewayServerConfig()
    discover_from: str = ""                # 인스턴스 yaml 디렉토리 (gateway_port 매칭)
    backend_api_key: str = ""              # vLLM --api-key 설정 시 내부 요청에 사용
    backends: list[BackendConfig] = []     # load_config에서 자동 생성
    health_check: HealthCheckConfig = HealthCheckConfig()
    warmup: WarmupConfig = WarmupConfig()
    prefix_cache_warmup: PrefixCacheWarmupConfig = PrefixCacheWarmupConfig()
    http_client: HttpClientConfig = HttpClientConfig()


def _resolve_actual_port(instance_dir: str, yaml_path: str, yaml_port: int) -> tuple[int, str]:
    """인스턴스의 실제 listen 포트를 결정한다.

    동작:
      - instance_dir/.runtime/<name>.json 이 존재하면 거기 기록된 port 사용 (launcher가 자동 회피한 실제 포트).
      - 없으면 yaml의 port를 fallback으로 사용 (vLLM이 아직 안 떴거나 port 자동 회피 미사용 구버전).

    반환: (actual_port, source) — source는 "runtime" 또는 "yaml"
    """
    name = os.path.splitext(os.path.basename(yaml_path))[0]
    runtime_path = os.path.join(instance_dir, ".runtime", f"{name}.json")
    if os.path.isfile(runtime_path):
        try:
            with open(runtime_path, encoding="utf-8") as f:
                data = json.load(f)
            if "port" in data:
                return int(data["port"]), "runtime"
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("runtime 파일 파싱 실패, yaml port 사용: %s (%s)", runtime_path, e)
    return int(yaml_port), "yaml"


def _discover_backends(config_path: str, raw: dict) -> list[dict]:
    """discover_from 디렉토리를 스캔해 gateway_port가 일치하는 인스턴스를 backends로 변환.

    동작:
      1. discover_from(상대 경로 → 절대 경로)의 *.yaml을 모두 읽는다.
      2. 각 yaml의 gateway_port가 자기 게이트웨이 포트와 일치하는 것만 채택.
      3. 실제 vLLM port는 instances/.runtime/<name>.json 우선 (launcher 자동 회피 결과),
         없으면 yaml의 port hint로 fallback.
      4. {host, port}로 변환하여 backends 리스트로 반환.
      5. 같은 게이트웨이로 묶인 인스턴스 간 실제 port 중복은 즉시 ValueError.

    그 외 yaml(gateway_port 미설정 / 다른 게이트웨이 소속)은 조용히 스킵.
    """
    gateway_port = raw.get("gateway", {}).get("port")
    if gateway_port is None:
        raise ValueError("discover_from 사용 시 gateway.port 명시 필수")

    config_dir = os.path.dirname(os.path.abspath(config_path))
    instance_dir = os.path.normpath(os.path.join(config_dir, raw["discover_from"]))
    if not os.path.isdir(instance_dir):
        raise FileNotFoundError(f"discover_from 디렉토리 없음: {instance_dir}")

    matched: list[tuple[str, dict]] = []
    for yaml_path in sorted(glob.glob(os.path.join(instance_dir, "*.yaml"))):
        try:
            with open(yaml_path, encoding="utf-8") as f:
                inst = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            logger.warning("yaml 파싱 실패, 스킵: %s (%s)", yaml_path, e)
            continue

        if inst.get("gateway_port") != gateway_port:
            continue
        if "port" not in inst:
            logger.warning("port 키 없음, 스킵: %s", yaml_path)
            continue
        matched.append((yaml_path, inst))

    # 실제 port 해석 (runtime 파일 우선, yaml fallback) + 중복 검증
    seen_ports: dict[int, str] = {}
    backends: list[dict] = []
    sources: list[str] = []
    for yaml_path, inst in matched:
        actual_port, source = _resolve_actual_port(instance_dir, yaml_path, inst["port"])
        if actual_port in seen_ports:
            raise ValueError(
                f"같은 gateway_port({gateway_port})로 묶인 인스턴스끼리 "
                f"실제 vLLM port {actual_port}가 중복됩니다: "
                f"{seen_ports[actual_port]} vs {yaml_path}"
            )
        seen_ports[actual_port] = yaml_path
        backends.append({"host": inst.get("host_for_gateway", "127.0.0.1"), "port": actual_port})
        sources.append(f"{os.path.basename(yaml_path)}→{actual_port}({source})")

    logger.info(
        "discover_from %s → gateway_port=%d 매칭 %d개: %s",
        instance_dir, gateway_port, len(backends), sources,
    )
    return backends


def load_config(path: str) -> GatewayConfig:
    """게이트웨이 YAML을 읽어 GatewayConfig로 파싱한다.

    백엔드 결정 우선순위:
      1) yaml에 backends 리스트 명시 → 그대로 사용 (수동 오버라이드 / 이질 라우팅)
      2) discover_from 명시 → 해당 디렉토리에서 gateway_port 매칭 자동 등록
    둘 다 없으면 ValueError로 fail-fast.
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 1) yaml에 backends 명시 → 그대로 사용
    if raw.get("backends"):
        logger.info(
            "backends 명시됨: %s",
            [f"{b.get('host', '127.0.0.1')}:{b['port']}" for b in raw["backends"]],
        )
        return GatewayConfig(**raw)

    # 2) discover_from 명시 → 디렉토리 스캔
    if raw.get("discover_from"):
        raw["backends"] = _discover_backends(path, raw)
        return GatewayConfig(**raw)

    raise ValueError(
        f"{path}: backends 또는 discover_from 중 하나는 명시해야 합니다. "
        "예: discover_from: '../instances'"
    )


# ═══════════════════════════════════════════════════════
# BackendServer — 백엔드 상태 추적
# ═══════════════════════════════════════════════════════


@dataclass
class BackendServer:
    """vLLM 백엔드 인스턴스 1대의 상태."""

    config: BackendConfig
    active_connections: int = 0
    is_healthy: bool = False
    is_ready: bool = False  # 웜업까지 완료된 상태
    is_warming_up: bool = False  # 웜업 진행 중 (중복 방지)
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_health_check: float = 0.0

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def label(self) -> str:
        return f"{self.config.host}:{self.config.port}"


# ═══════════════════════════════════════════════════════
# LoadBalancer — Least-Connection
# ═══════════════════════════════════════════════════════


class LoadBalancer:
    """Least-Connection 방식 로드밸런서.

    asyncio.Lock으로 스레드 안전성을 보장한다.
    is_ready=True인 서버만 라우팅 대상에 포함한다.
    """

    def __init__(self, servers: list[BackendServer]) -> None:
        self._servers = servers
        self._lock = asyncio.Lock()

    async def acquire(self) -> BackendServer:
        """활성 연결이 가장 적은 ready 서버를 선택한다.

        ready 서버가 없으면 503 에러를 위해 RuntimeError를 발생시킨다.
        """
        async with self._lock:
            ready = [s for s in self._servers if s.is_ready]
            if not ready:
                raise RuntimeError("사용 가능한 백엔드 서버가 없습니다")
            server = min(ready, key=lambda s: s.active_connections)
            server.active_connections += 1
            return server

    async def release(self, server: BackendServer) -> None:
        """요청 완료 후 연결 카운트를 감소시킨다."""
        async with self._lock:
            server.active_connections = max(0, server.active_connections - 1)

    @property
    def servers(self) -> list[BackendServer]:
        return self._servers

    @property
    def ready_count(self) -> int:
        return sum(1 for s in self._servers if s.is_ready)


# ═══════════════════════════════════════════════════════
# WarmupManager — 서버 웜업 (CUDA + 프리픽스 캐시)
# ═══════════════════════════════════════════════════════


class WarmupManager:
    """서버 기동/재기동 시 웜업을 수행한다.

    2단계 웜업:
    1. 서버 웜업 — 더미 추론으로 CUDA 커널 예열
    2. 프리픽스 캐시 웜업 — 시스템 프롬프트 KV 캐시 사전 적재

    모델명은 백엔드의 GET /v1/models에서 자동 감지한다.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        warmup_config: WarmupConfig,
        prefix_config: PrefixCacheWarmupConfig,
        api_key: str = "",
    ) -> None:
        self._client = client
        self._warmup = warmup_config
        self._prefix = prefix_config
        self._auth_headers: dict[str, str] = (
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        )

    async def warmup_all(self, servers: list[BackendServer]) -> None:
        """현재 떠있는 서버만 웜업한다. 나머지는 HealthChecker가 감지."""
        if not self._warmup.enabled:
            logger.info("웜업 비활성화 — health 프로브만 수행")
            for s in servers:
                try:
                    resp = await self._client.get(f"{s.base_url}/health", timeout=2.0)
                    if resp.status_code == 200:
                        s.is_healthy = True
                        s.is_ready = True
                except httpx.HTTPError:
                    pass
            return

        # 빠른 프로브: 현재 떠있는 서버 감지
        live: list[BackendServer] = []
        for s in servers:
            try:
                resp = await self._client.get(f"{s.base_url}/health", timeout=2.0)
                if resp.status_code == 200:
                    live.append(s)
            except httpx.HTTPError:
                pass

        if not live:
            # 아무도 안 떠있으면 첫 번째 서버만 boot_poll 대기
            logger.info(
                "기동된 백엔드 없음 — 첫 번째 서버 대기 (최대 %ds)",
                self._warmup.boot_poll.timeout_seconds,
            )
            await self._warmup_single(servers[0])
        else:
            logger.info("기동된 서버 %d/%d대 감지 — 웜업 시작", len(live), len(servers))
            tasks = [self._warmup_single(s) for s in live]
            await asyncio.gather(*tasks, return_exceptions=True)
        # 미기동 서버는 HealthChecker가 자동 감지 → rewarmup

    async def warmup_single(self, server: BackendServer) -> None:
        """단일 서버 웜업 (재기동 시 HealthChecker가 호출)."""
        await self._warmup_single(server)

    async def _warmup_single(self, server: BackendServer) -> None:
        """단일 서버 웜업 시퀀스."""
        label = server.label
        server.is_warming_up = True
        try:
            # 1. /health 대기
            await self._wait_for_health(server)

            # 2. 모델명 감지
            model_name = await self._detect_model(server)
            logger.info("[%s] 모델 감지: %s", label, model_name)

            # 3. CUDA 웜업 (더미 추론)
            await self._run_inference_warmup(server, model_name)

            # 4. 프리픽스 캐시 웜업
            await self._run_prefix_cache_warmup(server, model_name)

            server.is_healthy = True
            server.is_ready = True
            logger.info("[%s] 웜업 완료 — 라우팅 풀 등록", label)

        except Exception:
            server.is_ready = False
            logger.exception("[%s] 웜업 실패", label)
        finally:
            server.is_warming_up = False

    async def _wait_for_health(self, server: BackendServer) -> None:
        """서버가 /health에 200을 반환할 때까지 폴링한다."""
        label = server.label
        poll = self._warmup.boot_poll
        deadline = time.monotonic() + poll.timeout_seconds
        logger.info("[%s] 서버 기동 대기 (최대 %ds)...", label, poll.timeout_seconds)

        while time.monotonic() < deadline:
            try:
                resp = await self._client.get(
                    f"{server.base_url}/health",
                    timeout=3.0,
                )
                if resp.status_code == 200:
                    logger.info("[%s] 서버 기동 확인", label)
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(poll.interval_seconds)

        raise TimeoutError(f"[{label}] 서버 기동 대기 타임아웃 ({poll.timeout_seconds}s)")

    async def _detect_model(self, server: BackendServer) -> str:
        """GET /v1/models에서 모델명을 자동 감지한다."""
        resp = await self._client.get(
            f"{server.base_url}/v1/models", timeout=10.0, headers=self._auth_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data", [])
        if not models:
            raise ValueError(f"[{server.label}] /v1/models 응답에 모델이 없습니다")
        return models[0]["id"]

    async def _run_inference_warmup(self, server: BackendServer, model: str) -> None:
        """더미 추론으로 CUDA 커널을 예열한다."""
        label = server.label
        cfg = self._warmup.inference
        logger.info("[%s] CUDA 웜업 시작...", label)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": cfg.prompt}],
            "max_tokens": cfg.max_tokens,
            "temperature": 0,
        }
        start = time.monotonic()
        resp = await self._client.post(
            f"{server.base_url}/v1/chat/completions",
            json=payload,
            headers=self._auth_headers,
            timeout=cfg.timeout_seconds,
        )
        resp.raise_for_status()
        elapsed = time.monotonic() - start
        logger.info("[%s] CUDA 웜업 완료 (%.1fs)", label, elapsed)

    async def _run_prefix_cache_warmup(self, server: BackendServer, model: str) -> None:
        """시스템 프롬프트의 KV 캐시를 사전 적재한다."""
        if not self._prefix.enabled or not self._prefix.system_prompt.strip():
            return

        label = server.label
        logger.info("[%s] 프리픽스 캐시 웜업 시작...", label)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._prefix.system_prompt.strip()},
                {"role": "user", "content": "준비"},
            ],
            "max_tokens": self._prefix.max_tokens,
            "temperature": 0,
        }
        start = time.monotonic()
        resp = await self._client.post(
            f"{server.base_url}/v1/chat/completions",
            json=payload,
            headers=self._auth_headers,
            timeout=self._prefix.timeout_seconds,
        )
        resp.raise_for_status()
        elapsed = time.monotonic() - start
        logger.info("[%s] 프리픽스 캐시 웜업 완료 (%.1fs)", label, elapsed)


# ═══════════════════════════════════════════════════════
# HealthChecker — 주기적 헬스체크 + 재기동 감지
# ═══════════════════════════════════════════════════════


class HealthChecker:
    """백그라운드 주기적 헬스체크.

    연속 실패/성공 threshold 기반으로 상태를 전환한다.
    unhealthy → healthy 전환 시 웜업을 재실행한다.
    """

    def __init__(
        self,
        servers: list[BackendServer],
        client: httpx.AsyncClient,
        config: HealthCheckConfig,
        warmup_manager: WarmupManager,
    ) -> None:
        self._servers = servers
        self._client = client
        self._config = config
        self._warmup = warmup_manager
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """헬스체크 백그라운드 태스크를 시작한다."""
        self._task = asyncio.create_task(self._check_loop())
        logger.info("헬스체크 시작 (간격: %ds)", self._config.interval_seconds)

    async def stop(self) -> None:
        """헬스체크 태스크를 중지한다."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("헬스체크 중지")

    async def _check_loop(self) -> None:
        """주기적으로 전체 서버를 헬스체크한다."""
        while True:
            await asyncio.sleep(self._config.interval_seconds)
            for server in self._servers:
                await self._check_single(server)

    async def _check_single(self, server: BackendServer) -> None:
        """개별 서버 헬스체크 및 상태 전환."""
        label = server.label
        ok = False
        try:
            resp = await self._client.get(
                f"{server.base_url}/health",
                timeout=self._config.timeout_seconds,
            )
            ok = resp.status_code == 200
        except httpx.HTTPError:
            ok = False

        server.last_health_check = time.monotonic()

        if ok:
            server.consecutive_failures = 0
            server.consecutive_successes += 1

            if not server.is_healthy and not server.is_warming_up and server.consecutive_successes >= self._config.healthy_threshold:
                # unhealthy → healthy 전환: 웜업 재실행
                logger.info("[%s] 서버 복구 감지 — 웜업 재실행", label)
                # 웜업을 비동기로 실행 (헬스체크 루프를 블로킹하지 않음)
                asyncio.create_task(self._rewarmup(server))
        else:
            server.consecutive_successes = 0
            server.consecutive_failures += 1

            if server.is_healthy and server.consecutive_failures >= self._config.unhealthy_threshold:
                server.is_healthy = False
                server.is_ready = False
                logger.warning("[%s] 서버 다운 감지 (연속 %d회 실패)", label, server.consecutive_failures)

    async def _rewarmup(self, server: BackendServer) -> None:
        """재기동된 서버를 웜업하고 라우팅 풀에 복귀시킨다."""
        try:
            await self._warmup.warmup_single(server)
        except Exception:
            logger.exception("[%s] 재웜업 실패", server.label)


# ═══════════════════════════════════════════════════════
# FastAPI 앱
# ═══════════════════════════════════════════════════════

# 전역 상태 (lifespan에서 초기화)
_lb: LoadBalancer
_client: httpx.AsyncClient
_health_checker: HealthChecker
_start_time: float


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """앱 수명주기: 기동 시 웜업/헬스체크, 종료 시 정리."""
    global _lb, _client, _health_checker, _start_time

    # ── 설정 로드 ──
    config_path = os.environ.get("VLLM_GATEWAY_CONFIG")
    if not config_path:
        raise RuntimeError(
            "VLLM_GATEWAY_CONFIG 환경변수가 설정되지 않았습니다. "
            "`python vllm_gateway.py -c gateways/<port>.yaml`로 실행하세요."
        )
    config = load_config(config_path)
    logger.info("설정 로드: %s", config_path)

    # ── httpx 클라이언트 (앱 수명주기로 관리) ──
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            config.http_client.timeout_seconds,
            connect=config.http_client.connect_timeout_seconds,
        ),
        limits=httpx.Limits(
            max_connections=config.http_client.max_connections,
            max_keepalive_connections=config.http_client.max_connections,
        ),
    )

    # ── 백엔드 서버 초기화 ──
    servers = [BackendServer(config=bc) for bc in config.backends]
    _lb = LoadBalancer(servers)
    logger.info("백엔드 %d개 등록: %s", len(servers), [s.label for s in servers])

    # ── 웜업 (백그라운드) ──
    # backend ready를 lifespan startup에서 await하면 첫 backend가 뜰 때까지 uvicorn이
    # listen을 시작하지 못한다(yield에 도달 못 함). backend가 안 떠있는 상태에서도
    # 게이트웨이가 health 응답·운영 명령을 받을 수 있어야 하므로 백그라운드 태스크로 분리.
    # 첫 요청이 들어왔는데 ready 서버가 없으면 LoadBalancer.acquire가 503을 발생시킨다.
    warmup_mgr = WarmupManager(
        _client, config.warmup, config.prefix_cache_warmup, config.backend_api_key,
    )
    warmup_task = asyncio.create_task(warmup_mgr.warmup_all(servers))

    def _log_warmup_result(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("웜업 백그라운드 태스크 실패", exc_info=exc)
        else:
            logger.info("웜업 백그라운드 태스크 완료 — %d/%d 서버 ready",
                        _lb.ready_count, len(servers))
    warmup_task.add_done_callback(_log_warmup_result)

    # ── 헬스체크 시작 ──
    _health_checker = HealthChecker(servers, _client, config.health_check, warmup_mgr)
    await _health_checker.start()

    _start_time = time.monotonic()
    logger.info("게이트웨이 시작 — :%d (웜업/헬스체크 백그라운드 진행)", config.gateway.port)

    yield

    # ── 종료 ──
    if not warmup_task.done():
        warmup_task.cancel()
        try:
            await warmup_task
        except (asyncio.CancelledError, Exception):
            pass
    await _health_checker.stop()
    await _client.aclose()
    logger.info("게이트웨이 종료")


app = FastAPI(title="vLLM Gateway", lifespan=lifespan)


# ── GET /health ──────────────────────────────────────


@app.get("/health")
async def health() -> JSONResponse:
    """게이트웨이 헬스체크. ready 서버 1개 이상이면 200, 아니면 503."""
    ready = _lb.ready_count
    total = len(_lb.servers)
    status = 200 if ready > 0 else 503
    return JSONResponse(
        content={"status": "ok" if ready > 0 else "unavailable", "ready": ready, "total": total},
        status_code=status,
    )


# ── GET /v1/models ───────────────────────────────────


@app.get("/v1/models")
async def list_models(request: Request) -> JSONResponse:
    """임의의 ready 서버에서 모델 목록을 가져온다."""
    try:
        server = await _lb.acquire()
    except RuntimeError:
        return JSONResponse(content={"error": "사용 가능한 백엔드 없음"}, status_code=503)

    try:
        headers: dict[str, str] = {}
        auth = request.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth
        resp = await _client.get(f"{server.base_url}/v1/models", timeout=10.0, headers=headers)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.HTTPError as e:
        logger.error("[%s] /v1/models 프록시 실패: %s", server.label, e)
        return JSONResponse(content={"error": "백엔드 연결 실패"}, status_code=502)
    finally:
        await _lb.release(server)


# ── POST /v1/chat/completions ────────────────────────


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    """OpenAI chat/completions 프록시 (스트리밍/논스트리밍)."""
    body = await request.body()

    # stream 여부 판단
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(content={"error": "잘못된 JSON"}, status_code=400)

    is_stream = payload.get("stream", False)

    # 서버 선택
    try:
        server = await _lb.acquire()
    except RuntimeError:
        return JSONResponse(
            content={"error": {"message": "사용 가능한 백엔드 서버가 없습니다", "type": "server_error"}},
            status_code=503,
        )

    url = f"{server.base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    # Authorization 헤더 패스스루 (백엔드 --api-key 설정 시 필요)
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth

    if is_stream:
        return await _proxy_streaming(server, url, body, headers)
    else:
        return await _proxy_non_streaming(server, url, body, headers)


async def _proxy_non_streaming(
    server: BackendServer,
    url: str,
    body: bytes,
    headers: dict[str, str],
) -> JSONResponse:
    """논스트리밍 프록시."""
    try:
        resp = await _client.post(url, content=body, headers=headers)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.TimeoutException:
        logger.error("[%s] 타임아웃", server.label)
        return JSONResponse(content={"error": "백엔드 타임아웃"}, status_code=504)
    except httpx.HTTPError as e:
        logger.error("[%s] 연결 실패: %s", server.label, e)
        return JSONResponse(content={"error": "백엔드 연결 실패"}, status_code=502)
    finally:
        await _lb.release(server)


async def _proxy_streaming(
    server: BackendServer,
    url: str,
    body: bytes,
    headers: dict[str, str],
) -> StreamingResponse | JSONResponse:
    """SSE 스트리밍 프록시. 상태 코드 확인 후 바이트 단위로 패스스루한다."""
    # 스트림 연결을 열고 상태 코드를 먼저 확인한다
    try:
        req = _client.build_request("POST", url, content=body, headers=headers)
        resp = await _client.send(req, stream=True)
    except httpx.TimeoutException:
        logger.error("[%s] 스트리밍 연결 타임아웃", server.label)
        await _lb.release(server)
        return JSONResponse(content={"error": "백엔드 타임아웃"}, status_code=504)
    except httpx.HTTPError as e:
        logger.error("[%s] 스트리밍 연결 실패: %s", server.label, e)
        await _lb.release(server)
        return JSONResponse(content={"error": "백엔드 연결 실패"}, status_code=502)

    # 백엔드 에러 시 상태 코드를 정확히 전달
    if resp.status_code != 200:
        error_body = await resp.aread()
        await resp.aclose()
        await _lb.release(server)
        try:
            return JSONResponse(content=json.loads(error_body), status_code=resp.status_code)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(content={"error": "백엔드 에러"}, status_code=resp.status_code)

    # 200 — SSE 스트리밍 시작
    async def _stream() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        except httpx.TimeoutException:
            logger.error("[%s] 스트리밍 타임아웃", server.label)
        except httpx.HTTPError as e:
            logger.error("[%s] 스트리밍 실패: %s", server.label, e)
        finally:
            await resp.aclose()
            await _lb.release(server)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── GET /server-status ───────────────────────────────


@app.get("/server-status")
async def server_status() -> JSONResponse:
    """백엔드 서버 상태 대시보드."""
    uptime = time.monotonic() - _start_time
    backends = []
    for s in _lb.servers:
        backends.append({
            "url": s.base_url,
            "is_healthy": s.is_healthy,
            "is_ready": s.is_ready,
            "active_connections": s.active_connections,
            "consecutive_failures": s.consecutive_failures,
        })
    return JSONResponse(content={
        "gateway": {"uptime_seconds": round(uptime, 1)},
        "backends": backends,
        "ready_count": _lb.ready_count,
        "total_count": len(_lb.servers),
    })


# ═══════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(description="vLLM Gateway")
    parser.add_argument(
        "-c", "--config",
        required=True,
        help="게이트웨이 설정 파일 경로 (예: gateways/5015.yaml)",
    )
    args = parser.parse_args()

    config_path = os.path.abspath(args.config)
    if not os.path.exists(config_path):
        logger.error("설정 파일 없음: %s", config_path)
        raise SystemExit(1)

    os.environ["VLLM_GATEWAY_CONFIG"] = config_path
    config = load_config(config_path)

    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
        log_level=config.gateway.log_level,
    )


if __name__ == "__main__":
    main()
