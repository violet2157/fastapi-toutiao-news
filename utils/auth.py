#整合 根据token查询用户，返回用户
from fastapi import Header, Depends, HTTPException
from sqlalchemy.ext.asyncio.session import AsyncSession
from config.db_config import get_db
from crud import  users
from starlette import status


async def get_current_user(
        authorization: str = Header(...,alias="Authorization"),
        db:AsyncSession = Depends(get_db)
):
    #token = authorization.split(" ")[1]
    token = authorization.replace("Bearer ","")
    user = await users.get_user_by_token(db,token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="无效令牌或者令牌已过期")
    return user
