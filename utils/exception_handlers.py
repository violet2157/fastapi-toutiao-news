from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from utils.exception import http_exception_handler, integrity_error_handler, sqlalchemy_error_handler, \
    general_exception_handler


def register_error_handlers(app):
    """
    要点：子类在前 父类在后 具体在前 抽象在后
    """
    app.add_exception_handler(HTTPException,http_exception_handler) #业务层报错
    app.add_exception_handler(IntegrityError,integrity_error_handler) #数据完整性约束
    app.add_exception_handler(SQLAlchemyError,sqlalchemy_error_handler) #数据库
    app.add_exception_handler(Exception,general_exception_handler) #兜底
    # from fastapi.exceptions import RequestValidationError
    # from starlette.exceptions import HTTPException
    # from starlette.status import HTTP_404_NOT_FOUND
    # from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT
    # from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR
    # from starlette.responses import JSONResponse