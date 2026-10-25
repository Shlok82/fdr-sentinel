import base64
from typing import Optional, Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration and environment settings."""

    # Default to production if unset (fail-safe principle)
    APP_ENV: Literal["development", "staging", "production"] = "production"
    
    # Reusing existing debug and demo flags
    DEBUG: bool = False
    DEMO_MODE: bool = True
    DEMO_SHOW_CREDENTIALS: bool = False

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    SECRET_KEY: str = "REPLACE_ME"
    DOCUMENT_MASTER_KEY: str = "REPLACE_ME"

    # Authentication & Session Security
    BCRYPT_ROUNDS: int = 12
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    SESSION_COOKIE_NAME: str = "fdr_session"
    CSRF_COOKIE_NAME: str = "fdr_csrf"
    SEED_PASSWORD: str = "REPLACE_ME"

    # Database URLs
    # Owner role used for running migrations and administrative maintenance
    DATABASE_OWNER_URL: str = "postgresql+psycopg://fdr_owner:fdr_owner_dev_password@localhost:5432/fdr_sentinel"

    # Runtime app role (restricted permissions; UPDATE/DELETE denied on audit_log)
    DATABASE_URL: str = "postgresql+psycopg://fdr_app_user:fdr_app_dev_password@localhost:5432/fdr_sentinel"

    # Test database URLs (used for isolated test suites)
    TEST_OWNER_DATABASE_URL: str = "postgresql+psycopg://fdr_owner:fdr_owner_dev_password@localhost:5432/fdr_sentinel_test"
    TEST_DATABASE_URL: str = "postgresql+psycopg://fdr_app_user:fdr_app_dev_password@localhost:5432/fdr_sentinel_test"

    UPLOAD_STORAGE_PATH: str = "./storage"

    # Backwards compatibility alias for ENVIRONMENT
    @property
    def ENVIRONMENT(self) -> str:
        return self.APP_ENV

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Refuse to start in non-development environments if secrets are unset, placeholders, or malformed."""
        if self.APP_ENV != "development":
            # 1. Validate SECRET_KEY
            if not self.SECRET_KEY or self.SECRET_KEY == "REPLACE_ME" or len(self.SECRET_KEY.strip()) < 32:
                raise ValueError(
                    f"Configuration error: SECRET_KEY must be configured with at least 32 characters in '{self.APP_ENV}' mode."
                )

            # 2. Validate DOCUMENT_MASTER_KEY
            if not self.DOCUMENT_MASTER_KEY or self.DOCUMENT_MASTER_KEY == "REPLACE_ME":
                raise ValueError(
                    f"Configuration error: DOCUMENT_MASTER_KEY must be configured in '{self.APP_ENV}' mode."
                )

            try:
                decoded = base64.b64decode(self.DOCUMENT_MASTER_KEY.strip(), validate=True)
                if len(decoded) != 32:
                    raise ValueError(
                        f"Configuration error: DOCUMENT_MASTER_KEY must decode to exactly 32 bytes (got {len(decoded)} bytes)."
                    )
            except Exception as exc:
                if "decode to exactly 32 bytes" in str(exc):
                    raise
                raise ValueError(
                    "Configuration error: DOCUMENT_MASTER_KEY must be a valid 32-byte base64-encoded string."
                )

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
