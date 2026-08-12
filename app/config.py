import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# Powers "New task from text" (app/gemini_client.py). Left optional (unlike
# SESSION_SECRET below) so the rest of the app keeps working if this isn't
# configured yet - the route itself checks for an empty key.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Explicit login allow-list (comma-separated emails) - replaces the old
# "any @prezlab.com email" domain check. Edit in .env; no redeploy needed.
ALLOWED_LOGIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ALLOWED_LOGIN_EMAILS", "").split(",") if e.strip()
}

if not SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET is not set. Generate one with `python -c \"import secrets; print(secrets.token_hex(32))\"` "
        "and add it to .env - this signs the session cookie, so it must not be left empty or default."
    )
