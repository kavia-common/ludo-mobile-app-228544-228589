from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorResponse(BaseModel):
    """Standard error envelope for API responses."""

    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-friendly error message.")
    request_id: Optional[str] = Field(default=None, description="Request correlation id.")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error details, if any.")


def _request_id_from_request(request: Request) -> Optional[str]:
    return getattr(getattr(request, "state", None), "request_id", None)


# PUBLIC_INTERFACE
def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers that produce consistent JSON errors."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        payload = ErrorResponse(
            code="http_error",
            message=str(exc.detail) if exc.detail else "HTTP error",
            request_id=_request_id_from_request(request),
            details={"status_code": exc.status_code},
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        payload = ErrorResponse(
            code="validation_error",
            message="Request validation failed",
            request_id=_request_id_from_request(request),
            details={"errors": exc.errors()},
        )
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        payload = ErrorResponse(
            code="internal_error",
            message="Internal server error",
            request_id=_request_id_from_request(request),
        )
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump())
