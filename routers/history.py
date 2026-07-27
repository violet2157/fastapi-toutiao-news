from fastapi import APIRouter,Depends,Query,HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from config.db_config import get_db
from models.users import User
from utils.auth import get_current_user
from crud import history
from schemas.history import HistoryAddRequest,HistoryNewsItemResponse,HistoryListResponse
from utils.response import success_response
from starlette import status

router = APIRouter(prefix="/api/history", tags=["history"])

@router.post("/add")
async def add_history(
        data:HistoryAddRequest,
        user:User = Depends(get_current_user),
        db:AsyncSession = Depends(get_db)
):
    r1 = await history.add_news_history(db,user.id,data.news_id)
    return success_response(message="添加历史记录成功",data=r1)

@router.get("/list")
async def get_history(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    rows, total = await history.get_history_list(db, user.id, page, page_size)
    history_list = [{
        **news.__dict__,
        "history_time": history_time,
        "history_id": history_id
    } for news, history_time, history_id in rows]
    has_more = total > page * page_size
    data = HistoryListResponse(list=history_list, total=total, hasMore=has_more)
    return success_response(message="获取历史列表成功", data=data)

@router.delete("/delete/{history_id}")
async def remove_history(
        history_id: int,
        user:User = Depends(get_current_user),
        db:AsyncSession = Depends(get_db)
):
    r1 = await history.remove_news_history(db, user.id, history_id)
    if not r1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="历史记录不存在")
    return success_response(message="删除历史记录成功")

@router.delete("/clear")
async def clear_history(
        user:User = Depends(get_current_user),
        db:AsyncSession = Depends(get_db)
):
    count = await history.remove_all_history(db, user.id)
    return success_response(message=f"共清空{count}条历史记录")