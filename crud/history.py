from datetime import datetime

from sqlalchemy import select,delete,func
from sqlalchemy.ext.asyncio import AsyncSession
from models.history import History
from models.news import News

# async def is_news_history(
#         da:AsyncSession,
#         user_id:int,
#         news_id:int
# ):
#     query = select(History).where(History.user_id == user_id, History.news_id == news_id)
#     r1 = await da.execute(query)
#     #是否有收藏记录
#     return r1.scalar_one_or_none() is not None

async def add_news_history(
        db:AsyncSession,
        user_id:int,
        news_id:int
):
    query = select(History).where(History.user_id == user_id, History.news_id == news_id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    if existing:
        #curr = update(History).where(History.user_id == user_id, History.news_id == news_id).values(viewed_at = datetime.now())
        existing.viewed_at = datetime.utcnow()
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        history = History(user_id=user_id,news_id=news_id)
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history

#获取历史列表：获取某个用户的历史列表+分页功能
async def get_history_list(
        db:AsyncSession,
        user_id:int,
        page:int = 1,
        page_size:int = 10,
):
    #总量 + 历史的新闻列表
    count_query = select(func.count()).where(History.user_id == user_id)
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    #获取历史列表  查询历史表和新闻表  需要联表查询join
    # select(查询主体模型类,字段别名).join(联合查询的模型类，联合查询的条件).where().order_by().offset().limit()
    #
    query = (select(News, History.viewed_at.label("history_time"),History.id.label("history_id"))
             .join(History,History.news_id == News.id)
             .where(History.user_id == user_id)
             .order_by(History.viewed_at.desc())
             .offset((page-1)*page_size).limit(page_size)
             )
    r1 = await db.execute(query)
    rows = r1.all()
    return rows,total

async def remove_news_history(
        db:AsyncSession,
        user_id:int,
        history_id:int
):
    stmt = delete(History).where(History.user_id == user_id, History.id == history_id)
    r1 = await db.execute(stmt)
    await db.commit()
    return r1.rowcount > 0



async def remove_all_history(
        db:AsyncSession,
        user_id:int
):
    stmt = delete(History).where(History.user_id == user_id)
    r1 = await db.execute(stmt)
    await db.commit()
    #返回一个删除的数量
    return r1.rowcount or 0