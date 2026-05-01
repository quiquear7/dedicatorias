from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


load_dotenv(override=False)


def _from_streamlit_secrets(key: str) -> Optional[str]:
    try:
        import streamlit as st
    except ImportError:
        return None
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        return None
    return None


def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    value = _from_streamlit_secrets(key)
    if value is not None:
        return value
    return os.environ.get(key, default)


@dataclass(frozen=True)
class AppConfig:
    ai_provider: str
    openai_api_key: Optional[str]
    google_api_key: Optional[str]
    storage_backend: str
    local_storage_root: Path
    s3_bucket: Optional[str]
    s3_endpoint: Optional[str]
    s3_access_key: Optional[str]
    s3_secret_key: Optional[str]
    s3_region: str
    app_password: Optional[str]

    @property
    def is_ai_ready(self) -> bool:
        if self.ai_provider == "openai":
            return bool(self.openai_api_key)
        if self.ai_provider == "gemini":
            return bool(self.google_api_key)
        return False

    @property
    def is_openai_ready(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def is_gemini_ready(self) -> bool:
        return bool(self.google_api_key)

    @property
    def is_storage_ready(self) -> bool:
        if self.storage_backend == "local":
            return True
        return all([self.s3_bucket, self.s3_endpoint, self.s3_access_key, self.s3_secret_key])


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    backend = (_get("STORAGE_BACKEND") or "local").lower()
    project_root = Path(__file__).resolve().parent.parent
    local_root = Path(_get("LOCAL_STORAGE_ROOT") or project_root / "data")
    google_key = _get("GOOGLE_API_KEY") or _get("GEMINI_API_KEY")
    openai_key = _get("OPENAI_API_KEY")
    explicit_provider = (_get("AI_PROVIDER") or "").lower().strip()
    if explicit_provider in {"openai", "gemini"}:
        provider = explicit_provider
    elif google_key and not openai_key:
        provider = "gemini"
    elif openai_key and not google_key:
        provider = "openai"
    else:
        provider = "openai"  # ambos o ninguno: openai por defecto
    return AppConfig(
        ai_provider=provider,
        openai_api_key=openai_key,
        google_api_key=google_key,
        storage_backend=backend,
        local_storage_root=local_root,
        s3_bucket=_get("S3_BUCKET"),
        s3_endpoint=_get("S3_ENDPOINT"),
        s3_access_key=_get("S3_ACCESS_KEY"),
        s3_secret_key=_get("S3_SECRET_KEY"),
        s3_region=_get("S3_REGION") or "auto",
        app_password=_get("APP_PASSWORD"),
    )


@lru_cache(maxsize=1)
def get_openai_client():
    from openai import OpenAI

    cfg = get_config()
    if not cfg.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY no está configurada. Añádela a tu .env o a .streamlit/secrets.toml."
        )
    return OpenAI(api_key=cfg.openai_api_key)


@lru_cache(maxsize=1)
def get_gemini_client():
    from google import genai

    cfg = get_config()
    if not cfg.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY no está configurada. Crea una clave gratuita en "
            "https://aistudio.google.com/apikey y añádela a tu .env."
        )
    return genai.Client(api_key=cfg.google_api_key)


@lru_cache(maxsize=1)
def get_storage():
    from core.storage import LocalStorage, S3Storage

    cfg = get_config()
    if cfg.storage_backend == "local":
        return LocalStorage(cfg.local_storage_root)
    if cfg.storage_backend == "s3":
        if not cfg.is_storage_ready:
            raise RuntimeError(
                "STORAGE_BACKEND=s3 pero faltan credenciales (S3_BUCKET, S3_ENDPOINT, "
                "S3_ACCESS_KEY, S3_SECRET_KEY)."
            )
        return S3Storage(
            bucket=cfg.s3_bucket,
            endpoint_url=cfg.s3_endpoint,
            access_key=cfg.s3_access_key,
            secret_key=cfg.s3_secret_key,
            region=cfg.s3_region,
        )
    raise RuntimeError(f"STORAGE_BACKEND desconocido: {cfg.storage_backend}")
