import os
from pathlib import Path
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent

def get_default_accounts_file() -> Path:
    if "ACCOUNTS_FILE" in os.environ:
        return Path(os.environ["ACCOUNTS_FILE"]).resolve()
    cwd_file = Path.cwd() / "accounts.json"
    if cwd_file.exists():
        return cwd_file.resolve()
    base_file = BASE_DIR / "accounts.json"
    if base_file.exists():
        return base_file.resolve()
    user_config = Path.home() / ".config" / "zed-ai-proxy" / "accounts.json"
    return user_config

class Settings(BaseModel):
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    accounts_file: Path = Field(default_factory=get_default_accounts_file)
    state_dir: Path = Field(default_factory=lambda: Path.home() / ".local" / "state" / "zed-ai-proxy")
    zed_api_url: str = Field(
        default_factory=lambda: os.getenv("ZED_API_URL", "https://cloud.zed.dev").rstrip("/")
    )
    zed_version: str = Field(default_factory=lambda: os.getenv("ZED_VERSION", "1.18.1"))
    token_refresh_interval_seconds: int = 60
    token_refresh_threshold_seconds: int = 300
    rate_limit_cooldown_seconds: int = 60
    max_image_size_bytes: int = 500_000

settings = Settings()
