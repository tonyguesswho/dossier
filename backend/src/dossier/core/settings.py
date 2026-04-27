from __future__ import annotations

import os
import pathlib
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(...)

    openrouter_api_key: SecretStr = Field(...)
    # Embeddings call api.openai.com directly — OpenRouter doesn't proxy /v1/embeddings.
    openai_api_key: SecretStr = Field(...)

    # Missing Langfuse keys downgrade tracing to a no-op.
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    exa_api_key: SecretStr = Field(...)
    firecrawl_api_key: SecretStr = Field(...)
    # GitHub falls back to unauthenticated (60 req/hr) when unset.
    github_token: SecretStr | None = None
    # NewsAPI / Crunchbase tools fail open with empty key.
    newsapi_api_key: str = ""
    crunchbase_api_key: str = ""

    clerk_jwks_url: str = ""
    dossier_auth_dev_bypass: str = ""
    dispatch_mode: Literal["local", "lambda"] = Field(
        default="local", alias="DOSSIER_DISPATCH_MODE"
    )
    lambda_function_name: str = ""

    @field_validator("database_url")
    @classmethod
    def _must_be_postgres(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgresql+psycopg://", "postgres://")):
            raise ValueError(
                f"database_url must be a Postgres URL. Got: {v[:30]}..."
            )
        return v

    @property
    def database_url_libpq(self) -> str:
        if self.database_url.startswith("postgresql+psycopg://"):
            return "postgresql://" + self.database_url[len("postgresql+psycopg://"):]
        return self.database_url

    @property
    def has_langfuse(self) -> bool:
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None

    @property
    def is_lambda_runtime(self) -> bool:
        return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

