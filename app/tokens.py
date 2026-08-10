"""Server-side-revocable refresh tokens, backing session persistence beyond the
signed session cookie's lifetime. See supabase/schema.sql for the auth_tokens
table this reads and writes - always through the service-role client, never
exposed to a browser-side Supabase client.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Response

from app.supabase_client import get_service_client

COOKIE_NAME = "refresh_token"
TTL = timedelta(days=30)


def issue(response: Response, user_id: str) -> None:
    """Mints a new refresh token, stores its hash, and sets it as a cookie on response."""
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + TTL

    get_service_client().table("auth_tokens").insert(
        {"user_id": user_id, "token_hash": _hash(raw_token), "expires_at": expires_at.isoformat()}
    ).execute()

    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=int(TTL.total_seconds()),
        httponly=True,
        samesite="lax",
    )


def resolve(raw_token: str) -> dict | None:
    """Returns {'id', 'email', 'full_name'} for a valid, non-revoked, unexpired token, else None."""
    now = datetime.now(timezone.utc).isoformat()
    rows = (
        get_service_client()
        .table("auth_tokens")
        .select("*, profiles(id, email, full_name)")
        .eq("token_hash", _hash(raw_token))
        .is_("revoked_at", "null")
        .gt("expires_at", now)
        .execute()
        .data
    )
    return rows[0]["profiles"] if rows else None


def revoke(raw_token: str) -> None:
    get_service_client().table("auth_tokens").update(
        {"revoked_at": datetime.now(timezone.utc).isoformat()}
    ).eq("token_hash", _hash(raw_token)).execute()


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()
