import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name = os.getenv("APP_NAME", "NotaFacil API")
    app_env = os.getenv("APP_ENV", "development")
    secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
    database_url = os.getenv("DATABASE_URL", "sqlite:///./notafacil.db")
    access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
    cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",")]
    debug_reset_code = os.getenv("DEBUG_RESET_CODE", "true").lower() == "true"

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "") or smtp_user
    assistant_provider = os.getenv("ASSISTANT_PROVIDER", "openrouter").strip().lower() or "openrouter"
    assistant_api_key = (
        os.getenv("ASSISTANT_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("OPENROUTER_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    assistant_model = (
        os.getenv("ASSISTANT_MODEL", "").strip()
        or os.getenv("OPENROUTER_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "meta-llama/llama-3.2-3b-instruct:free"
    )
    assistant_fallback_models = [
        item.strip()
        for item in os.getenv(
            "ASSISTANT_FALLBACK_MODELS",
            "meta-llama/llama-3.2-3b-instruct:free,qwen/qwen3-next-80b-a3b-instruct:free,meta-llama/llama-3.3-70b-instruct:free",
        ).split(",")
        if item.strip()
    ]
    assistant_site_url = os.getenv("ASSISTANT_SITE_URL", "https://digiai-finance-beta.onrender.com").strip()
    assistant_app_title = os.getenv("ASSISTANT_APP_TITLE", "DiGiaI Caixa").strip()


@lru_cache
def get_settings():
    return Settings()
