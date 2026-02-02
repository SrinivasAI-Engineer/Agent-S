from __future__ import annotations
import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Get the backend directory (parent of app directory)
BACKEND_DIR = Path(__file__).parent.parent
ENV_FILE = BACKEND_DIR / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse .env file; return dict of KEY=value. Used so backend/.env wins over system env vars."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1].replace('\\"', '"')
            elif len(val) >= 2 and val[0] == "'" and val[-1] == "'":
                val = val[1:-1].replace("\\'", "'")
            out[key] = val
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    app_env: str = "dev"
    app_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"

    database_url: str = "sqlite:///./agentsocials.db"

    tokens_fernet_key: str = ""
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"


    firecrawl_api_key: str = ""
    firecrawl_api_base: str = "https://api.firecrawl.dev"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-pro"

    twitter_client_id: str = ""
    twitter_client_secret: str = ""
    twitter_redirect_uri: str = "http://localhost:8000/v1/oauth/twitter/callback"

    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/v1/oauth/linkedin/callback"


_settings = Settings()
# Override from backend/.env so the file always wins (e.g. Windows env quirks)
_env_overrides = _read_env_file(ENV_FILE)
def _get(key: str) -> str:
    return (_env_overrides.get(key) or "").strip()
if _get("GEMINI_API_KEY"):
    _settings.gemini_api_key = _get("GEMINI_API_KEY")
if _get("GEMINI_MODEL"):
    _settings.gemini_model = _get("GEMINI_MODEL")
if _get("TWITTER_CLIENT_ID"):
    _settings.twitter_client_id = _get("TWITTER_CLIENT_ID")
if _get("TWITTER_CLIENT_SECRET"):
    _settings.twitter_client_secret = _get("TWITTER_CLIENT_SECRET")
if _get("TWITTER_REDIRECT_URI"):
    _settings.twitter_redirect_uri = _get("TWITTER_REDIRECT_URI")
if _get("LINKEDIN_CLIENT_ID"):
    _settings.linkedin_client_id = _get("LINKEDIN_CLIENT_ID")
if _get("LINKEDIN_CLIENT_SECRET"):
    _settings.linkedin_client_secret = _get("LINKEDIN_CLIENT_SECRET")
if _get("LINKEDIN_REDIRECT_URI"):
    _settings.linkedin_redirect_uri = _get("LINKEDIN_REDIRECT_URI")
if _get("TOKENS_FERNET_KEY"):
    _settings.tokens_fernet_key = _get("TOKENS_FERNET_KEY")
settings = _settings

