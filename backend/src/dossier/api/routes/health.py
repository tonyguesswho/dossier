from __future__ import annotations
# /healthz is intentionally public; no DB ping.

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}
