"""
RxTrail — Central configuration.

Loads secrets & settings from the workspace-root .env file (the REAL keys live
there and it is git-ignored). Unknown keys in .env (Telegram, Brave, etc. that
belong to other tools sharing the same .env) are ignored.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/  -> workspace root (where .env lives)
BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── MongoDB ──────────────────────────────────────────────────────────
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db: str = "rxtrail"

    # ── JWT / auth ───────────────────────────────────────────────────────
    jwt_secret: str = "rxtrail-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_hours: int = 168  # 7 days — PWA needs long-lived sessions

    # ── Auth policy ──────────────────────────────────────────────────────
    admin_email: str = "skouras@infosphereco.com"  # μόνο αυτός βλέπει το /admin
    allow_open_registration: bool = False  # εγγραφές μόνο από admin (anti-spam)

    # ── Web Push (VAPID) ──────────────────────────────────────────────────
    vapid_subject: str = "mailto:admin@rxtrail.top"
    vapid_private_key: str = ""
    vapid_public_key_b64url: str = ""  # για applicationServerKey στο frontend

    # ── DeepSeek V4 (LLM articulations) ──────────────────────────────────
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-pro"       # κύριο μοντέλο — ποιότητα + σύνθεση
    deepseek_model_deep: str = "deepseek-v4-pro"  # deep, για weekly insight
    deepseek_model_fast: str = "deepseek-v4-flash"  # ταξινόμηση/δρομολόγηση — γρήγορο
    deepseek_thinking: bool = False

    # ── Jina (embeddings / rerank / reader) ── legacy fallback ──────────
    jina_api_key: str = ""
    jina_embeddings_url: str = "https://api.jina.ai/v1/embeddings"
    jina_rerank_url: str = "https://api.jina.ai/v1/rerank"
    jina_embeddings_model: str = "jina-embeddings-v3"

    # ── Τοπικά embeddings (δωρεάν — αντικαθιστούν το Jina) ───────────────
    local_embeddings_model: str = "intfloat/multilingual-e5-small"

    # ── App ──────────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000,https://rxtrail.top,https://www.rxtrail.top"
    upload_dir: str = str(ROOT_DIR / "uploads")
    language: str = "el"
    llm_timeout_seconds: float = 30.0
    photo_retention_days: int = 30
    brave_search_api_key: str = ""

    # ── Server ───────────────────────────────────────────────────────────
    server_host: str = "127.0.0.1"
    server_port: int = 8000

    # ── Business logic tunables ──────────────────────────────────────────
    VALUE_CAP: int = 500           # REQ-03: max monthly value for score factor
    REPEAT_MIN_GAP_HOURS: int = 24 # REQ-05: idempotency guard for repeat
    CONTACT_DUE_BONUS: float = 5.0 # REQ-06: score bonus for contact due today
    FULFIL_SLA_DAYS: int = 7       # REQ-01: deadline for prescription execution

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
