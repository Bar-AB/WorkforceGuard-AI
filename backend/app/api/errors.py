from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.errors import ConflictError, DomainError, InvalidInputError, NotFoundError

ErrorHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ConflictError: status.HTTP_409_CONFLICT,
}


def register_error_handlers(app: FastAPI) -> None:
    for error_type, status_code in _STATUS_BY_ERROR.items():
        app.add_exception_handler(error_type, _responder(status_code))


def _responder(status_code: int) -> ErrorHandler:
    async def respond(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(error)})

    return respond
