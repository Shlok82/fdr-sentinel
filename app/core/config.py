import base64
from typing import Optional, Literal
from zoneinfo import ZoneInfo
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

    UPLOAD_STORAGE_PATH: str = "/app/storage"
    VAULT_PATH: str = "/app/storage"
    MAX_UPLOAD_MB: int = 10
    MOCK_BANK_LATENCY_MS: int = 800

    # Temporal & Localization Settings
    DISPLAY_TIMEZONE: str = "Asia/Kolkata"

    # Temporal Engine & Scheduler Automation (Tier 2)
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_MINUTES: int = 15
    EXPIRING_WINDOW_DAYS: int = 90
    REMINDER_THRESHOLDS_DAYS: str = "90,60,30,7"
    TIER2_MAX_DAYS: int = 30
    URGENT_MAX_DAYS: int = 7
    OVERDUE_ESCALATION_DAYS: int = 15
    PENDING_REVIEW_ALERT_DAYS: int = 7
    UNRESOLVED_FLAG_ALERT_DAYS: int = 14

    @property
    def reminder_thresholds(self) -> list[int]:
        """Parsed list of integer reminder thresholds (guaranteed positive, strictly decreasing, unique)."""
        raw = [x.strip() for x in self.REMINDER_THRESHOLDS_DAYS.split(",") if x.strip()]
        return [int(x) for x in raw]

    @property
    def display_tz(self) -> ZoneInfo:
        return ZoneInfo(self.DISPLAY_TIMEZONE)

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

        # Validate DISPLAY_TIMEZONE
        try:
            ZoneInfo(self.DISPLAY_TIMEZONE)
        except Exception as exc:
            raise ValueError(f"Configuration error: Invalid DISPLAY_TIMEZONE '{self.DISPLAY_TIMEZONE}': {exc}")

        # Validate Temporal & Scheduler Settings (Tier 2)
        if self.SCHEDULER_INTERVAL_MINUTES < 1:
            raise ValueError("Configuration error: SCHEDULER_INTERVAL_MINUTES must be at least 1.")

        for day_field in [
            "EXPIRING_WINDOW_DAYS",
            "TIER2_MAX_DAYS",
            "URGENT_MAX_DAYS",
            "OVERDUE_ESCALATION_DAYS",
            "PENDING_REVIEW_ALERT_DAYS",
            "UNRESOLVED_FLAG_ALERT_DAYS",
        ]:
            val = getattr(self, day_field)
            if not isinstance(val, int) or val <= 0:
                raise ValueError(f"Configuration error: {day_field} must be a positive integer (> 0).")

        raw_thresholds = [x.strip() for x in self.REMINDER_THRESHOLDS_DAYS.split(",") if x.strip()]
        if not raw_thresholds:
            raise ValueError("Configuration error: REMINDER_THRESHOLDS_DAYS must be a comma-separated list of positive integers.")

        parsed_thresholds: list[int] = []
        for x in raw_thresholds:
            try:
                parsed_thresholds.append(int(x))
            except ValueError:
                raise ValueError(f"Configuration error: REMINDER_THRESHOLDS_DAYS contains non-integer threshold '{x}'.")

        for t in parsed_thresholds:
            if t <= 0:
                raise ValueError("Configuration error: REMINDER_THRESHOLDS_DAYS must contain only positive integers (> 0).")

        if len(parsed_thresholds) != len(set(parsed_thresholds)):
            raise ValueError("Configuration error: REMINDER_THRESHOLDS_DAYS contains duplicate threshold values.")

        for i in range(len(parsed_thresholds) - 1):
            if parsed_thresholds[i] <= parsed_thresholds[i + 1]:
                raise ValueError("Configuration error: REMINDER_THRESHOLDS_DAYS must be strictly decreasing.")

        if self.EXPIRING_WINDOW_DAYS < parsed_thresholds[0]:
            raise ValueError(
                f"Configuration error: EXPIRING_WINDOW_DAYS ({self.EXPIRING_WINDOW_DAYS}) cannot be less than the largest reminder threshold ({parsed_thresholds[0]})."
            )

        if self.TIER2_MAX_DAYS >= self.EXPIRING_WINDOW_DAYS:
            raise ValueError(
                f"Configuration error: TIER2_MAX_DAYS ({self.TIER2_MAX_DAYS}) must be strictly less than EXPIRING_WINDOW_DAYS ({self.EXPIRING_WINDOW_DAYS})."
            )

        if self.URGENT_MAX_DAYS >= self.TIER2_MAX_DAYS:
            raise ValueError(
                f"Configuration error: URGENT_MAX_DAYS ({self.URGENT_MAX_DAYS}) must be strictly less than TIER2_MAX_DAYS ({self.TIER2_MAX_DAYS})."
            )

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
