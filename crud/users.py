from sqlalchemy import select,update,delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.users import User, UserToken#模型类
from schemas.users import UserRequest, UserUpdateRequest #请求体
from utils import security #密码加密
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status
import uuid
from datetime import datetime,timedelta


#根据用户名查询数据库
async def get_user_by_username(db:AsyncSession,username:str):
    query = select(User).where(User.username == username)
    r1 = await db.execute(query)
    user = r1.scalar_one_or_none()
    return user

#创建用户
async def create_user(db:AsyncSession,user_data:UserRequest):
    #先加密密码-> add
    hashed_password = security.get_hash_password(user_data.password)
    user = User(username=user_data.username, password=hashed_password)
    db.add(user)
    # 先flush拿到数据库生成的id，但不commit（由get_db统一提交）
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户已存在"
        )
    return user

#生成token
async def create_token(db:AsyncSession,user_id: int):
    # 每次登录都新发一把 token（插一行），而不是覆盖这个用户的旧 token。
    # 原因：原来是「一个用户只保留一行，登录时覆盖旧的」，于是
    #   「在手机上登录 → 电脑上那把 token 立刻失效 → 电脑继续请求就 401」。
    # 改成每次插入新行后，手机、电脑等多设备可以同时保持登录。
    token = str(uuid.uuid4())
    expires_at = datetime.now() + timedelta(days=3)
    db.add(UserToken(user_id=user_id, token=token, expires_at=expires_at))

    # 顺手清掉这个用户已过期的 token，避免表被历史 token 撑大
    await db.execute(
        delete(UserToken).where(
            UserToken.user_id == user_id,
            UserToken.expires_at < datetime.now(),
        )
    )

    # 只flush不commit，由get_db统一提交，避免事务管理混乱
    await db.flush()
    return token

#登录验证用户
async def authenticate_user(db:AsyncSession,username:str, password:str):
    user = await get_user_by_username(db,username)
    if not user:
        return None
    if not security.verify_password(password, user.password):
        return None
    return user

#根据Token查询用户：验证Token->查询用户
async def get_user_by_token(db:AsyncSession,token:str):
    query = select(UserToken).where(UserToken.token == token)
    r1 = await db.execute(query)
    db_token = r1.scalar_one_or_none()
    if not db_token or db_token.expires_at < datetime.now():
        return None
    query = select(User).where(User.id == db_token.user_id)
    r1 = await db.execute(query)
    return r1.scalar_one_or_none()

#更新用户信息:update更新  检查是否命中  获取更新后的用户返回
async def update_user(db:AsyncSession,username:str,user_data:UserUpdateRequest):
    #user_data是一个pydantic类型，得到字典**就能解包成键值对
    # update(User).where(User.username == username).values(字段=值，字段=值)
    #没有设置值的不更新
    query = update(User).where(User.username == username).values(**user_data.model_dump(
        exclude_none=True,
        exclude_unset=True
    ))
    r1 = await db.execute(query)
    # 检查是否命中（在commit之前检查rowcount）
    if r1.rowcount == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 统一由get_db提交事务
    await db.flush()
    # 获取更新后的用户信息
    updated_user = await get_user_by_username(db, username)
    return updated_user

#修改密码：验证旧密码  新密码加密  修改密码
async def change_password(db:AsyncSession, user:User, old_password:str, new_password:str):
    if not security.verify_password(old_password, user.password):
        return False
    # 从当前session重新查询，避免跨session操作detached对象
    current_user = await get_user_by_username(db, user.username)
    hashnewpwd = security.get_hash_password(new_password)
    current_user.password = hashnewpwd
    await db.flush()
    await db.refresh(current_user)
    return True