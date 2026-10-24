from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    DEMO_MODE: bool = True

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    SECRET_KEY: str = "dev_secret_key_change_in_production_min_32_chars_long"
    DOCUMENT_MASTER_KEY: str = "k8T0a7Z3Yx9L1q4W7e2R5t8Y1u4I7o0P3s6D9f2G5h8="

    # Database URLs
    # Owner role used for running migrations and administrative maintenance
    DATABASE_OWNER_URL: str = "postgresql+psycopg://fdr_owner:fdr_owner_dev_password@localhost:5432/fdr_sentinel"

    # Runtime app role (restricted permissions; UPDATE/DELETE denied on audit_log)
    DATABASE_URL: str = "postgresql+psycopg://fdr_app_user:fdr_app_dev_password@localhost:5432/fdr_sentinel"

    UPLOAD_STORAGE_PATH: str = "./storage"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
