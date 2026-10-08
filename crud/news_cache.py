from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession, result
from sqlalchemy import select, func, update, false

from cache.new_cache import get_cache_categories, set_cache_categories, get_cache_news_list, set_cache_news_list, \
    set_cache_news_detail, get_cache_news_detail, get_cache_news_count, set_cache_news_count, \
    get_cache_related_news, set_cache_related_news
from models.news import Category, News
from schemas.base import NewsItemBase

# 头条分类的 id。头条不是中新网的独立频道，而是「全部分类最新新闻」的聚合推荐：
# 查询头条时不按 category_id 过滤，直接返回全库按发布时间倒序的最新新闻。
HEADLINE_CATEGORY_ID = 1


#查询列表
async def get_categories(db,skip: int = 0, limit: int = 100):
    #先尝试从缓存中获取数据
    cached_categories = await get_cache_categories()
    if cached_categories:
        return cached_categories
    stmt = select(Category).offset(skip).limit(limit)
    r1 = await db.execute(stmt)
    categories = r1.scalars().all()
    if categories:
        categories1 = jsonable_encoder(categories)
        await set_cache_categories(categories1)
    return categories

async def get_news_list(db:AsyncSession,category_id:int,skip: int = 0, limit: int = 10):
    #读取缓存列表
    page = skip//limit+1
    cache_list = await get_cache_news_list(category_id, page,limit)
    # if cache_list:
    #     return [News(**item) for item in cache_list]
    if cache_list:
        return cache_list

    #查询的是指定分类下的所有新闻
    # 按发布时间倒序：最新的新闻排在最上面（publish_time 越大越新）
    stmt = select(News)
    # 头条 = 聚合推荐：不按分类过滤，返回全库最新新闻（其余分类才加 category_id 过滤）
    if category_id != HEADLINE_CATEGORY_ID:
        stmt = stmt.where(News.category_id == category_id)
    stmt = stmt.order_by(News.publish_time.desc()).offset(skip).limit(limit)
    r1 = await db.execute(stmt)
    news_list = r1.scalars().all()
    #by_alias=True 使用别名（驼峰字段），和前端 NewsItem.vue 里用的 publishTime/categoryId 对齐
    if news_list :
        news_data = [NewsItemBase.model_validate(item).model_dump(mode="json",by_alias=True) for item in news_list]
        await set_cache_news_list(category_id, page,limit,news_data)
        return news_data

    return []
#聚合分类的总对象数
async def get_news_count(db:AsyncSession,category_id:int):
    cached_count = await get_cache_news_count(category_id)
    if cached_count is not None:
        return cached_count
    stmt = select(func.count(News.id))
    # 头条 = 聚合推荐：统计全库总数（其余分类才加 category_id 过滤）
    if category_id != HEADLINE_CATEGORY_ID:
        stmt = stmt.where(News.category_id == category_id)
    r1 = await db.execute(stmt)
    count = r1.scalar_one()
    await set_cache_news_count(category_id, count)
    return count
#根据id获取具体信息
async def get_news_detail(db:AsyncSession,news_id:int):
    cache_detail = await get_cache_news_detail(news_id)
    if cache_detail and "content" in cache_detail:
        return cache_detail
    stmt = select(News).where(News.id == news_id)
    r1 = await db.execute(stmt)
    news_item = r1.scalars().first()
    if news_item:
        news_data = NewsItemBase.model_validate(
            news_item
        ).model_dump(mode="json", by_alias=False)
        news_data["content"] = news_item.content
        await set_cache_news_detail(news_id, news_data)
        return news_data
    return None
#浏览量增长
async def increase_news_views(db:AsyncSession,news_id:int):
    # 先原子自增：views = views + 1（SQL 层面自增，并发下也不会少算）
    await db.execute(update(News).where(News.id == news_id).values(views=News.views + 1))
    await db.commit()
    # 再查一次最新值返回。MySQL 的 UPDATE 不支持 RETURNING，
    # 只有查回来才能拿到「加完之后的阅读数」，让详情接口实时回显；
    # 否则返回的永远是加之前的值，前端看起来就像「阅读数一直不涨」。
    result = await db.execute(select(News.views).where(News.id == news_id))
    return result.scalar_one()
#相关推荐
async def get_related_news(db:AsyncSession,news_id:int,category_id:int,limit: int = 5):
    cached_related = await get_cache_related_news(news_id, category_id, limit)
    if cached_related:
        return cached_related
    stmt = select(News).where(
        News.category_id == category_id,
        News.id != news_id,
    ).order_by(
        News.publish_time.desc()
    ).limit(limit)
    r1 = await db.execute(stmt)
    related = r1.scalars().all()
    if related:
        related_data = jsonable_encoder(related)
        await set_cache_related_news(news_id, category_id, limit, related_data)
    return related