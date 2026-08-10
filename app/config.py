import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

if not SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET is not set. Generate one with `python -c \"import secrets; print(secrets.token_hex(32))\"` "
        "and add it to .env - this signs the session cookie, so it must not be left empty or default."
    )
