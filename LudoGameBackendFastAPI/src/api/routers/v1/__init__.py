from __future__ import annotations

from fastapi import APIRouter

from src.api.routers.v1.auth import router as auth_router
from src.api.routers.v1.health import router as health_router
from src.api.routers.v1.lobbies import router as lobbies_router
from src.api.routers.v1.matchmaking import router as matchmaking_router
from src.api.routers.v1.players import router as players_router
from src.api.routers.v1.safe_chat import router as safe_chat_router
from src.api.routers.v1.sessions import router as sessions_router
from src.api.routers.v1.social import router as social_router

router = APIRouter()
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(players_router)
router.include_router(social_router)
router.include_router(safe_chat_router)

# Step 4: matchmaking, lobbies, sessions/presence, reconnect
router.include_router(matchmaking_router)
router.include_router(lobbies_router)
router.include_router(sessions_router)
