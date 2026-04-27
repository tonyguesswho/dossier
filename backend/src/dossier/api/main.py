from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dossier.api.routes import health, investigations

app = FastAPI(title="Dossier API", version="0.1.0")

# TODO: env-driven origin list once Vercel deployment URL is fixed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(investigations.router)


__all__ = ["app"]
