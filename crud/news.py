from sqlalchemy.ext.asyncio import AsyncSession, result
from sqlalchemy import select,func,update
from models.news import Category, News

#查询列表
async def get_categories(db,skip: int = 0, limit: int = 100):
    stmt = select(Category).offset(skip).limit(limit)
    r1 = await db.execute(stmt)
    return r1.scalars().all()

async def get_news_list(db:AsyncSession,category_id:int,skip: int = 0, limit: int = 10):
    #查询的是指定分类下的所有新闻
    stmt = select(News).where(News.category_id == category_id).offset(skip).limit(limit)
    r1 = await db.execute(stmt)
    return r1.scalars().all()
#聚合分类的总对象数
async def get_news_count(db:AsyncSession,category_id:int):
    stmt = select(func.count(News.id)).where(News.category_id == category_id)
    r1 = await db.execute(stmt)
    return r1.scalar_one() #只能有一个结果否则报错
#根据id获取具体信息
async def get_news_detail(db:AsyncSession,news_id:int):
    stmt = select(News).where(News.id == news_id)
    r1 = await db.execute(stmt)
    return r1.scalar_one_or_none()
#浏览量增长
async def increase_news_views(db:AsyncSession,news_id:int):
    stmt = update(News).where(News.id == news_id).values(views=News.views + 1)
    r1 = await db.execute(stmt)
    await db.commit()
    #更新 检查数据库是否命中了函数->命中为True
    return r1.rowcount > 0 #rowcount可以获取到命中的行数
#相关推荐
async def get_related_news(db:AsyncSession,news_id:int,category_id:int,limit: int = 5):
    stmt = select(News).where(
        News.category_id == category_id,
        News.id != news_id,
    ).order_by(
        News.publish_time.desc() #默认升序 改为降序
    ).limit(limit)
    r1 = await db.execute(stmt)
    return r1.scalars().all()
    # related_news = r1.scalars().all()
# RAG检索：关键词搜索新闻（标题+描述）
async def search_news(db: AsyncSession, keyword: str, limit: int = 5):
    like_pattern = f"%{keyword}%"
    stmt = (
        select(News)
        .where(
            News.title.ilike(like_pattern) | News.description.ilike(like_pattern)
        )
        .order_by(News.publish_time.desc())
        .limit(limit)
    )
    r1 = await db.execute(stmt)
    return r1.scalars().all()