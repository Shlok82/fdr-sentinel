import hmac
import secrets
import bcrypt
import jwt
from datetime import timedelta
from typing import Optional, Dict, Any
from app.core.config import settings
from app.services.clock_service import clock_service

# Precomputed 72-byte safe dummy bcrypt hash for timing mitigation on unknown users
DUMMY_BCRYPT_HASH = "$2b$12$e8kZl1.1Kz8jN4E3i5S1eOq.7JdM4V7T3I0o2P5s8D1f4G7h0K3m."


def hash_password(password: str) -> str:
    """Hashes a password using bcrypt directly with configured rounds."""
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError("Password exceeds bcrypt maximum length limit of 72 bytes.")
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies password against bcrypt hash, strictly rejecting >72 byte inputs."""
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > 72:
        return False
    try:
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


def dummy_verify_password(plain_password: str) -> bool:
    """Performs a dummy bcrypt verification against a known hash to mitigate user enumeration timing."""
    verify_password(plain_password, DUMMY_BCRYPT_HASH)
    return False


def create_access_token(user_id: str | Any) -> str:
    """Generates signed JWT access token with claims iat and exp derived from real_now()."""
    # Note: real_now() is strictly used here so Time Machine jumps don't invalidate session tokens
    now = clock_service.real_now()
    exp = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodes and validates JWT token, strictly enforcing HS256 algorithm."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None


def generate_csrf_token() -> str:
    """Generates cryptographically random token for double-submit CSRF defense."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(submitted_token: Optional[str], cookie_token: Optional[str]) -> bool:
    """Constant-time verification of submitted CSRF token against cookie token."""
    if not submitted_token or not cookie_token:
        return False
    return hmac.compare_digest(submitted_token.strip(), cookie_token.strip())


def is_safe_redirect_url(url: Optional[str]) -> bool:
    """Validates that a redirect target is a same-site relative path."""
    if not url:
        return False
    # Must start with exactly one forward slash, followed by valid path chars
    if url.startswith("/") and not url.startswith("//") and not url.startswith("/\\"):
        return True
    return False
