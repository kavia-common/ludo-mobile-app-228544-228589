from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="Health check (v1)")
# PUBLIC_INTERFACE
def health_v1() -> dict:
    """Health check endpoint for versioned API."""
    return {"status": "ok", "version": "v1"}
