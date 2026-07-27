from fastapi import APIRouter, Depends, HTTPException #路由 依赖 异常
from sqlalchemy.ext.asyncio import AsyncSession #异步引擎
from config.db_config import get_db #异步会话 依赖注入
#请求体参数
from schemas.users import UserRequest, UserAuthResponse, UserInfoResponse, UserUpdateRequest, UserChangePassword
from crud import  users #crud逻辑
from starlette import status #
from utils.response import success_response #封装相应格式
from utils.auth import get_current_user #获取token
from models.users import User #定义基础模型类
router = APIRouter(prefix="/api/user", tags=["users"])

@router.post("/register")
async def get_register(user_data: UserRequest,db:AsyncSession = Depends(get_db)):
    existing_user = await users.get_user_by_username(db,user_data.username)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="用户已存在")
    user = await users.create_user(db,user_data)
    token = await users.create_token(db,user.id)
    #注册逻辑：验证用户是否存在-> 创建用户-> 生成token-> 响应结果
    # return {
    #     "code":200,
    #     "message":"注册成功",
    #     "data":{
    #         "token":token,
    #         "userInfo":{
    #             "id":user.id,
    #             "username":user.username,
    #             "bio": user.bio,
    #             "avatar":user.avatar,
    #         }
    #     }
    # }
    response_date = UserAuthResponse(token=token,user_info=UserInfoResponse.model_validate(user))
    return success_response(message="注册成功",data=response_date)

@router.post("/login")
async def login(user_data: UserRequest,db:AsyncSession = Depends(get_db)):
    #登录逻辑：验证用户是否存在 -> 验证密码-> 生成token-> 响应结果
    user = await users.authenticate_user(db,user_data.username,user_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="用户名或密码错误")
    token = await users.create_token(db,user.id)
    response_data = UserAuthResponse(token=token,user_info=UserInfoResponse.model_validate(user))
    return success_response(message="登陆成功",data=response_data)

@router.get("/info")
async def get_user_info(user:User = Depends(get_current_user)):
    #查token查用户-> 封装crud-> 功能整合成一个工具函数-> 路由导入使用：依赖注入
    return success_response(message="用户信息获取成功",data=UserInfoResponse.model_validate(user))

@router.put("/update")
#修改用户信息:验证token->更新（用户输入数据 put提交->请求体参数->定义pydantic模型类）->响应结果
async def update_user_info(user_data:UserUpdateRequest, user:User = Depends(get_current_user),
                           db:AsyncSession = Depends(get_db)):
    updated_user = await users.update_user(db, user.username, user_data)
    return success_response(message="修改成功！", data=UserInfoResponse.model_validate(updated_user))

@router.put("/password")
async def update_user_password(password_data:UserChangePassword, user:User = Depends(get_current_user),
                               db:AsyncSession = Depends(get_db)):
    res_change_pwd = await users.change_password(db, user, password_data.old_password, password_data.new_password)
    if not(res_change_pwd):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,detail = "修改失败，请稍后重试")
    return success_response(message = "修改密码成功！")