"""Runtime configuration; secrets never enter the browser or versioned files."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COUNTERPART_", extra="ignore")
    database_url: str = "postgresql:///counterpart_dev"
    media_dir: Path = Path("var/media")
    web_dir: Path = Path("web/dist")
    host: str = "127.0.0.1"
    port: int = 8080
    live_enabled: bool = False
    roboflow_model: str = "coco/39"
    inference_limit: int = Field(default=128, ge=0, le=10000)
    inference_concurrency: int = Field(default=2, ge=1, le=4)
    inference_global_limit: int = Field(default=4, ge=1, le=16)
    credentials_file: Path = Path.home() / ".config/coveragecv/credentials.json"
    export_project: str = ""
    export_enabled: bool = False
    max_upload_bytes: int = 24 * 1024 * 1024
    max_asset_bytes: int = 2 * 1024 * 1024
    max_archive_bytes: int = Field(default=512 * 1024 * 1024, ge=1024 * 1024, le=8 * 1024**3)
    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    s3_bucket: str = ""
    s3_prefix: str = "counterpart/media"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_allow_insecure: bool = False
    require_operator: bool = False
    operator_token: SecretStr = SecretStr("")
    operator_cookie_secure: bool = False
    operator_local_http: bool = False
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    allowed_origins: list[str] = [
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]
    operator_cookie_ttl: int = Field(default=8 * 3600, ge=60, le=24 * 3600)

    def credentials(self) -> dict[str, SecretStr]:
        """Return only the required private provider fields, never serialize this result."""
        import os

        values = json.loads(self.credentials_file.read_text()) if self.credentials_file.exists() else {}
        return {
            key: SecretStr(os.getenv(key, values.get(key, "")))
            for key in ("ROBOFLOW_API_KEY", "ROBOFLOW_WORKSPACE")
        }


@lru_cache
def settings() -> Settings:
    config = Path.home() / ".config/counterpart/runtime.json"
    values = json.loads(config.read_text()) if config.exists() else {}
    # Environment overrides the private config. Settings normally prioritizes constructor args.
    import os

    values = {k: v for k, v in values.items() if f"COUNTERPART_{k.upper()}" not in os.environ}
    return Settings(**values)
