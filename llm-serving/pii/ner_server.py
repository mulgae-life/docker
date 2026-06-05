"""GPU NER 추론 서버 — transformers token-classification.

한 프로세스 = 한 모델. LB는 같은 모델 replica를 여러 포트로 띄워 프록시가 분산한다.
모델별 label 체계 차이(townboy 67 / vmaca 7)는 `entity_group`(raw)으로 그대로
반환하고, PII 타입 통합 매핑은 호출 측(`detectors.ner_client`)에서 처리한다
— 서버는 모델 불가지론적으로 둔다(SRP).

기동 예:
  CUDA_VISIBLE_DEVICES=3 python ner_server.py \
      --model-path /models/PII/vmaca123/korean-pii-ner-v3 \
      --port 8911 --model-tag vmaca123 --device cuda
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    pipeline,
)


class NerRequest(BaseModel):
    text: str


class Entity(BaseModel):
    entity_group: str  # 모델 raw 라벨 (예: NAME, LC_ADDRESS, QT_CARD_NUMBER)
    start: int
    end: int
    word: str
    score: float


# 모듈 전역(프로세스 수명) — 요청마다 로드 금지(리소스 수명주기).
_state: dict = {"pipe": None, "tag": "", "ready": False}


def _resolve_device(device: str) -> int:
    """transformers pipeline의 device 인자(int)로 변환. cuda=0(CUDA_VISIBLE_DEVICES로 격리), cpu=-1."""
    if device.startswith("cuda"):
        return 0  # CUDA_VISIBLE_DEVICES로 물리 GPU를 격리하는 운영 전제
    return -1


def build_app(model_path: str, model_tag: str, device: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # 모델 로드(프로세스 시작 1회). 실패 시 fail-fast(기동 중단).
        tok = AutoTokenizer.from_pretrained(model_path)
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        mdl = AutoModelForTokenClassification.from_pretrained(model_path, torch_dtype=dtype)
        _state["pipe"] = pipeline(
            "ner",
            model=mdl,
            tokenizer=tok,
            aggregation_strategy="simple",  # BIO를 엔티티 span으로 병합
            device=_resolve_device(device),
        )
        _state["tag"] = model_tag
        _state["ready"] = True
        yield
        _state["ready"] = False

    app = FastAPI(title=f"PII NER ({model_tag})", lifespan=lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        ok = _state["ready"]
        return JSONResponse({"status": "ok" if ok else "loading", "model": _state["tag"]},
                            status_code=200 if ok else 503)

    @app.post("/ner")
    async def ner(req: NerRequest) -> JSONResponse:
        if not _state["ready"]:
            return JSONResponse({"error": "model loading"}, status_code=503)
        raw = _state["pipe"](req.text)
        ents = [
            Entity(
                entity_group=str(e["entity_group"]),
                start=int(e["start"]),
                end=int(e["end"]),
                word=str(e["word"]),
                score=float(e["score"]),
            ).model_dump()
            for e in raw
        ]
        return JSONResponse({"model": _state["tag"], "entities": ents})

    return app


def main() -> None:
    p = argparse.ArgumentParser(description="PII NER 추론 서버")
    p.add_argument("--model-path", required=True)
    p.add_argument("--model-tag", default="ner")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--device", default="cuda")
    args = p.parse_args()

    app = build_app(args.model_path, args.model_tag, args.device)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
