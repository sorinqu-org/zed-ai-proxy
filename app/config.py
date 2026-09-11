import os
from pathlib import Path
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseModel):
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    accounts_file: Path = Field(
        default_factory=lambda: Path(os.getenv("ACCOUNTS_FILE", str(BASE_DIR / "accounts.json")))
    )
    zed_api_url: str = Field(
        default_factory=lambda: os.getenv("ZED_API_URL", "https://api.zed.dev").rstrip("/")
    )
    zed_version: str = Field(default_factory=lambda: os.getenv("ZED_VERSION", "1.18.1"))
    token_refresh_interval_seconds: int = 60
    token_refresh_threshold_seconds: int = 300
    rate_limit_cooldown_seconds: int = 60
    max_image_size_bytes: int = 500_000

settings = Settings()
