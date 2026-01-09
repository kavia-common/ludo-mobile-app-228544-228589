"""
Compatibility entrypoint.

The repository currently runs the backend from `LudoGameBackendFastAPI/`.
The work item declares the container root as `LudoGameBackend(FastAPI)/`.

To keep preview stable while aligning structure, we re-export the ASGI app
from the existing implementation.
"""

from __future__ import annotations

# Re-export stable entrypoint
from src.api.main import app  # noqa: F401
