"""Pydantic schemas for API request/response payloads."""

# Re-export schema modules for convenience.
# (Importing here is optional; routes import directly.)
from src.api.schemas.auth import *  # noqa: F401,F403
from src.api.schemas.matchmaking import *  # noqa: F401,F403
from src.api.schemas.social import *  # noqa: F401,F403
from src.api.schemas.gameplay import *  # noqa: F401,F403
from src.api.schemas.store import *  # noqa: F401,F403
