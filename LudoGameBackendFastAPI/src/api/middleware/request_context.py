from __future__ import annotations

import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Add a request id to every request and log request timing.

    - Uses incoming X-Request-ID if present; otherwise generates one.
    - Adds X-Request-ID to response headers.
    - Adds X-Process-Time-MS to response headers.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        start = time.perf_counter()

        # Attach to request.state for downstream usage.
        request.state.request_id = request_id

        try:
            response: Response = await call_next(request)
        except Exception:
            # Let centralized exception handlers format the error response;
            # we still log timing and request id here.
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "Unhandled exception during request",
                extra={"request_id": request_id},
            )
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "%s %s -> %s (%sms)",
                request.method,
                request.url.path,
                getattr(getattr(response, "status_code", None), "__str__", lambda: "error")(),
                duration_ms,
                extra={"request_id": request_id},
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Process-Time-MS"] = str(duration_ms)
        return response
