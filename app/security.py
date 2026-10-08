"""API-key authentication via the X-API-Key header."""
import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import get_settings

_header = APIKeyHeader(name="X-API-Key", auto_error=False)  # also adds the Authorize button in /docs


def require_api_key(supplied: str | None = Security(_header)) -> None:
    ok = False
    if supplied:
        candidate = supplied.encode("utf-8", "ignore")
        for key in get_settings().api_key_list:
            # compare_digest = constant time, so response timing can't leak the key.
            # Comparing bytes (not str) because compare_digest raises on non-ASCII str.
            ok |= hmac.compare_digest(candidate, key.encode("utf-8"))
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")
