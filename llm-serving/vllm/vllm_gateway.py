#!/usr/bin/env python3
"""vLLM Gateway — 로드밸런싱 + 헬스체크 + 웜업 리버스 프록시.

다수의 vLLM 인스턴스 앞단에서 Least-Connection 로드밸런싱,
주기적 헬스체크, 기동/재기동 시 자동 웜업을 제공한다.

구조:
    클라이언트 → Gateway(:5015) → vLLM(:7070, :7071, ...)

포트 관리:
    vLLM 인스턴스 포트는 vllm_config.yaml에서 단일 관리.
    게이트웨이가 vllm_config.yaml을 읽어 base_port를 자동 감지하고,
    다중 인스턴스는 base_port + index로 자동 할당한다.

사용법:
    python vllm_gateway.py
    python vllm_gateway.py --config vllm_gateway_config.yaml

    # 백그라운드 실행
    mkdir -p logs && nohup python vllm_gateway.py > logs/gateway.log 2>&1 &
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
DEFAULT_CONFIG = os.path.join(BASE_DIR, "vllm_gateway_config.yaml")
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
    vllm_config: str = "vllm_config.yaml"
    gateway: GatewayServerConfig = GatewayServerConfig()
    backend_count: int = 1
    backend_api_key: str = ""  # vLLM --api-key 설정 시 내부 요청에 사용
    backends: list[BackendConfig] = []  # load_config에서 자동 생성
    health_check: HealthCheckConfig = HealthCheckConfig()
    warmup: WarmupConfig = WarmupConfig()
    prefix_cache_warmup: PrefixCacheWarmupConfig = PrefixCacheWarmupConfig()
    http_client: HttpClientConfig = HttpClientConfig()


def load_config(path: str) -> GatewayConfig:
    """게이트웨이 YAML + vllm_config.yaml을 읽어 GatewayConfig로 파싱한다.

    vllm_config.yaml에서 port(base_port)를 읽어
    backend_count만큼 base_port + index로 백엔드를 자동 구성한다.
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # vllm_config.yaml에서 base_port 읽기
    config_dir = os.path.dirname(os.path.abspath(path))
    vllm_config_rel = raw.get("vllm_config", "vllm_config.yaml")
    vllm_config_path = os.path.join(config_dir, vllm_config_rel)

    base_port = 8000  # vLLM 기본 포트
    if os.path.exists(vllm_config_path):
        with open(vllm_config_path, encoding="utf-8") as f:
            vllm_raw = yaml.safe_load(f) or {}
        base_port = vllm_raw.get("port", base_port)
        logger.info("vllm_config 로드: %s (base_port=%d)", vllm_config_path, base_port)
    else:
        logger.warning("vllm_config 없음: %s (base_port=%d 사용)", vllm_config_path, base_port)

    # backend_count만큼 백엔드 자동 생성
    count = raw.get("backend_count", 1)
    raw["backends"] = [{"host": "127.0.0.1", "port": base_port + i} for i in range(count)]

    return GatewayConfig(**raw)


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
    config_path = os.environ.get("VLLM_GATEWAY_CONFIG", DEFAULT_CONFIG)
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

    # ── 웜업 ──
    warmup_mgr = WarmupManager(
        _client, config.warmup, config.prefix_cache_warmup, config.backend_api_key,
    )
    await warmup_mgr.warmup_all(servers)

    ready = _lb.ready_count
    logger.info("웜업 완료 — %d/%d 서버 ready", ready, len(servers))

    # ── 헬스체크 시작 ──
    _health_checker = HealthChecker(servers, _client, config.health_check, warmup_mgr)
    await _health_checker.start()

    _start_time = time.monotonic()
    logger.info("게이트웨이 시작 — :%d", config.gateway.port)

    yield

    # ── 종료 ──
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
        default=DEFAULT_CONFIG,
        help=f"게이트웨이 설정 파일 경로 (기본: {DEFAULT_CONFIG})",
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
