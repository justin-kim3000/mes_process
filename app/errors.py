"""업무 에러와 공통 응답 형식."""
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

# MariaDB 에러 번호: UNIQUE 제약 위반
ER_DUP_ENTRY = 1062


class BusinessError(Exception):
    """업무 규칙 위반. {"detail": {"code", "message"}} 형식으로 응답한다."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


def not_found(message: str) -> BusinessError:
    return BusinessError("NOT_FOUND", message, 404)


def duplicate_code(message: str) -> BusinessError:
    return BusinessError("DUPLICATE_CODE", message, 409)


def invalid_item_type(message: str) -> BusinessError:
    return BusinessError("INVALID_ITEM_TYPE", message, 400)


def wo_invalid_status(message: str) -> BusinessError:
    return BusinessError("WO_INVALID_STATUS", message, 409)


def is_duplicate_key(exc: IntegrityError) -> bool:
    """IntegrityError가 UNIQUE 제약 위반인지 확인."""
    args = getattr(exc.orig, "args", ())
    return bool(args) and args[0] == ER_DUP_ENTRY
