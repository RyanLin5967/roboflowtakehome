"""Runtime configuration; secrets never enter the browser or versioned files."""

import json
from functools import lru_cache
from pathlib import Path

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
    credentials_file: Path = Path.home() / ".config/coveragecv/credentials.json"
    export_project: str = ""
    export_enabled: bool = False
    max_upload_bytes: int = 24 * 1024 * 1024
    max_asset_bytes: int = 2 * 1024 * 1024

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
