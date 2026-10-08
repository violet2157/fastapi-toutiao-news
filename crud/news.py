from sqlalchemy.ext.asyncio import AsyncSession, result
from sqlalchemy import select,func,update,text
from sqlalchemy.dialects.mysql import match as mysql_match
from models.news import Category, News

# 头条分类的 id：头条是「全部分类最新新闻」的聚合推荐，查询时不按分类过滤
HEADLINE_CATEGORY_ID = 1

#查询列表
async def get_categories(db,skip: int = 0, limit: int = 100):
    stmt = select(Category).offset(skip).limit(limit)
    r1 = await db.execute(stmt)
    return r1.scalars().all()

async def get_news_list(db:AsyncSession,category_id:int,skip: int = 0, limit: int = 10):
    #查询的是指定分类下的所有新闻
    # 按发布时间倒序：最新的新闻排在最上面
    stmt = select(News)
    # 头条 = 聚合推荐：不按分类过滤，返回全库最新新闻
    if category_id != HEADLINE_CATEGORY_ID:
        stmt = stmt.where(News.category_id == category_id)
    stmt = stmt.order_by(News.publish_time.desc()).offset(skip).limit(limit)
    r1 = await db.execute(stmt)
    return r1.scalars().all()
#聚合分类的总对象数
async def get_news_count(db:AsyncSession,category_id:int):
    stmt = select(func.count(News.id))
    # 头条 = 聚合推荐：统计全库总数
    if category_id != HEADLINE_CATEGORY_ID:
        stmt = stmt.where(News.category_id == category_id)
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
# ========== 新闻检索（AI 助手搜索用）==========
# 索引名/参与检索的列，建索引和查询时要保持一致
_FULLTEXT_INDEX_NAME = "ft_news_search"
_FULLTEXT_COLUMNS = "title, description, content"

# 进程内的「已经确认过索引存在」标记。查 information_schema 是额外一次往返，
# 没必要每次搜索都查；建过一次之后直接跳过（索引建好后不会自己消失）。
_fulltext_ready = False


async def ensure_fulltext_index(db: AsyncSession) -> bool:
    """确保 news 表上有全文索引，没有就自动建一个。返回「全文检索能不能用」。

    为什么要 WITH PARSER ngram？
        MySQL 默认的全文分词是「按空格/标点切词」，中文一整句话切不出词，
        索引里就是空的。ngram 解析器改成「按固定字数滑窗切」（ngram_token_size
        默认 2，也就是按两个字一组切），中文才能搜出东西。

    为什么自动建、而不是让用户手动执行 SQL？
        少一步人工操作，就少一个「忘了建索引导致搜不到」的坑。

    查/建都失败时返回 False（不抛异常），调用方会退回到 LIKE 模糊匹配，
    搜索功能仍然可用，只是排序没有相关度那么准。
    """
    global _fulltext_ready
    if _fulltext_ready:
        return True
    try:
        # information_schema.STATISTICS 是 MySQL 记录「哪个表有哪些索引」的系统表。
        # 一个三列的索引在这里是 3 行，所以只判有没有（非 0），不关心具体行数。
        exists = await db.scalar(
            text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'news' "
                "AND INDEX_NAME = :name"
            ),
            {"name": _FULLTEXT_INDEX_NAME},
        )
        if not exists:
            # DDL 语句。1056 行的表建索引就是几毫秒，放在首次搜索时做也不卡
            await db.execute(
                text(
                    f"ALTER TABLE news ADD FULLTEXT INDEX {_FULLTEXT_INDEX_NAME} "
                    f"({_FULLTEXT_COLUMNS}) WITH PARSER ngram"
                )
            )
            await db.commit()
        _fulltext_ready = True
        return True
    except Exception as e:
        print(f"全文索引不可用，搜索退化为 LIKE 模糊匹配: {e}")
        return False


async def search_news(db: AsyncSession, keyword: str, limit: int = 5):
    """按关键词搜索新闻，返回 [(News, 相关度分数), ...]，相关度高的排前面。

    两条路配合：
      1. 全文索引 MATCH ... AGAINST —— MySQL 会算一个相关度分数，
         标题里命中比正文里命中分高，所以结果比 LIKE 排序更合理。
      2. LIKE 兜底 —— 全文索引不可用，或者索引搜不出结果时用。
         索引搜不出结果很正常：ngram 按 2 个字切词，搜「车」这种单个字
         切不出词来，全文索引必然返回空；LIKE 还能按字面匹配捞回来。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    if await ensure_fulltext_index(db):
        # mysql_match 编译出来就是 MATCH (title, description, content) AGAINST (%s)
        score = mysql_match(News.title, News.description, News.content, against=keyword)
        stmt = (
            select(News, score.label("score"))
            .where(score)  # 自然语言模式下，相关度为 0 的行本来就不会被返回
            .order_by(score.desc(), News.publish_time.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        if rows:
            return [(row[0], float(row[1])) for row in rows]

    like_pattern = f"%{keyword}%"
    stmt = (
        select(News)
        .where(
            News.title.ilike(like_pattern) | News.description.ilike(like_pattern)
        )
        .order_by(News.publish_time.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    # LIKE 没有相关度分数，统一给 0，让调用方不用区分两种结果的结构
    return [(n, 0.0) for n in rows]