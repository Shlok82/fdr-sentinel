import os
import base64
import pytest
from app.core.config import Settings


def test_default_app_env_is_production():
    """Fail-safe default: when APP_ENV is unset, it defaults to production."""
    # Temporarily remove APP_ENV from environment if present
    old_env = os.environ.pop("APP_ENV", None)
    try:
        # With placeholders, instantiating in default production mode must fail
        with pytest.raises(ValueError) as exc_info:
            Settings(
                _env_file=None,
                SECRET_KEY="REPLACE_ME",
                DOCUMENT_MASTER_KEY="REPLACE_ME",
            )
        assert "production" in str(exc_info.value).lower()
    finally:
        if old_env is not None:
            os.environ["APP_ENV"] = old_env


def test_production_refuses_placeholder_secret_key():
    """Production mode must refuse startup when SECRET_KEY is REPLACE_ME or empty."""
    valid_master_key = base64.b64encode(os.urandom(32)).decode()
    
    with pytest.raises(ValueError) as exc_info:
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="REPLACE_ME",
            DOCUMENT_MASTER_KEY=valid_master_key,
        )
    err_msg = str(exc_info.value)
    assert "SECRET_KEY must be configured" in err_msg
    # Ensure secrets are never echoed in error logs
    assert "REPLACE_ME" not in err_msg


def test_production_refuses_placeholder_master_key():
    """Production mode must refuse startup when DOCUMENT_MASTER_KEY is REPLACE_ME."""
    valid_secret = "a" * 48
    
    with pytest.raises(ValueError) as exc_info:
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY=valid_secret,
            DOCUMENT_MASTER_KEY="REPLACE_ME",
        )
    err_msg = str(exc_info.value)
    assert "DOCUMENT_MASTER_KEY must be configured" in err_msg
    assert "REPLACE_ME" not in err_msg


def test_production_refuses_malformed_master_key():
    """Production mode must refuse startup if DOCUMENT_MASTER_KEY does not decode to exactly 32 bytes."""
    valid_secret = "a" * 48
    # 16 bytes encoded instead of 32
    short_key = base64.b64encode(os.urandom(16)).decode()
    
    with pytest.raises(ValueError) as exc_info:
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY=valid_secret,
            DOCUMENT_MASTER_KEY=short_key,
        )
    err_msg = str(exc_info.value)
    assert "32 bytes" in err_msg
    # Confirm secret value is not echoed
    assert short_key not in err_msg


def test_production_accepts_valid_secrets():
    """Production mode accepts valid keys meeting security specifications."""
    valid_secret = "a" * 48
    valid_master_key = base64.b64encode(os.urandom(32)).decode()

    cfg = Settings(
        _env_file=None,
        APP_ENV="production",
        SECRET_KEY=valid_secret,
        DOCUMENT_MASTER_KEY=valid_master_key,
    )
    assert cfg.APP_ENV == "production"
    assert cfg.ENVIRONMENT == "production"


def test_development_permits_placeholders():
    """Development mode permits placeholder values for rapid local setup."""
    cfg = Settings(
        _env_file=None,
        APP_ENV="development",
        SECRET_KEY="REPLACE_ME",
        DOCUMENT_MASTER_KEY="REPLACE_ME",
    )
    assert cfg.APP_ENV == "development"
