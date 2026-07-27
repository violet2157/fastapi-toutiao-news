from sqlalchemy import select,delete,func
from sqlalchemy.ext.asyncio import AsyncSession
from models.favorite import Favorite
from models.news import News


async def is_news_favorite(
        da:AsyncSession,
        user_id:int,
        news_id:int
):
    query = select(Favorite).where(Favorite.user_id == user_id, Favorite.news_id == news_id)
    r1 = await da.execute(query)
    #是否有收藏记录
    return r1.scalar_one_or_none() is not None

async def add_news_favorite(
        db:AsyncSession,
        user_id:int,
        news_id:int
):
    favorite = Favorite(user_id=user_id,news_id=news_id)
    db.add(favorite)
    await db.commit()
    await db.refresh(favorite)
    return favorite

async def remove_news_favorite(
        db:AsyncSession,
        user_id:int,
        news_id:int
):
    stmt = delete(Favorite).where(Favorite.user_id == user_id, Favorite.news_id == news_id)
    r1 = await db.execute(stmt)
    await db.commit()
    return r1.rowcount > 0

#获取收藏列表：获取某个用户的收藏列表+分页功能
async def get_favorite_list(
        db:AsyncSession,
        user_id:int,
        page:int = 1,
        page_size:int = 10,
):
    #总量 + 收藏的新闻列表
    count_query = select(func.count()).where(Favorite.user_id == user_id)
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    #获取收藏列表  查询收藏表和新闻表  需要联表查询join
    # select(查询主体模型类,字段别名).join(联合查询的模型类，联合查询的条件).where().order_by().offset().limit()
    #
    query = (select(News, Favorite.created_at.label("favorite_time"),Favorite.id.label("favorite_id"))
             .join(Favorite,Favorite.news_id == News.id)
             .where(Favorite.user_id == user_id)
             .order_by(Favorite.created_at.desc())
             .offset((page-1)*page_size).limit(page_size)
             )
    r1 = await db.execute(query)
    rows = r1.all()
    return rows,total

async def remove_all_favorite(
        db:AsyncSession,
        user_id:int
):
    stmt = delete(Favorite).where(Favorite.user_id == user_id)
    r1 = await db.execute(stmt)
    await db.commit()
    #返回一个删除的数量
    return r1.rowcount or 0