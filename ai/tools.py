from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from ai.vector_store import get_retriever
from crud import favorite, history, news
from models.users import User


def create_tools(db: AsyncSession, user: User):
    """创建 Agent 工具集，注入数据库会话和当前用户。"""
    retriever = get_retriever()

    @tool
    async def search_news(query: str) -> str:
        """语义搜索新闻。当用户想查找某类新闻、询问某个话题相关的新闻时使用。
        参数 query: 搜索关键词或问题描述。"""
        docs = await retriever.ainvoke(query)
        if not docs:
            return "未找到相关新闻。"
        items = []
        for i, doc in enumerate(docs, 1):
            title = doc.metadata.get("title", "无标题")
            news_id = doc.metadata.get("news_id", "")
            items.append(f"{i}. [{news_id}] {title}\n   {doc.page_content[:200]}...")
        return "\n\n".join(items)

    @tool
    async def get_news_detail(news_id: int) -> str:
        """获取指定新闻的详细内容。当用户想看某条新闻的完整内容时使用。
        参数 news_id: 新闻ID。"""
        detail = await news.get_news_detail(db, news_id)
        if not detail:
            return f"新闻ID {news_id} 不存在。"
        await news.increase_news_views(db, news_id)
        return f"标题：{detail.title}\n作者：{detail.author or '未知'}\n发布时间：{detail.publish_time}\n内容：{detail.content}"

    @tool
    async def get_categories() -> str:
        """获取所有新闻分类列表。当用户想了解有哪些新闻分类时使用。"""
        cats = await news.get_categories(db)
        if not cats:
            return "暂无分类。"
        return "\n".join(f"- {c.name} (ID:{c.id})" for c in cats)

    @tool
    async def get_favorites(page: int = 1, page_size: int = 10) -> str:
        """获取当前用户的收藏列表。当用户想看自己的收藏、我的收藏时使用。
        参数 page: 页码，默认1。page_size: 每页数量，默认10。"""
        rows, total = await favorite.get_favorite_list(db, user.id, page, page_size)
        if not rows:
            return "你还没有收藏任何新闻。"
        items = []
        for news_obj, fav_time, fav_id in rows:
            items.append(f"- [{fav_id}] {news_obj.title} (收藏于 {fav_time})")
        return f"共 {total} 条收藏（第{page}页）：\n" + "\n".join(items)

    @tool
    async def get_history(page: int = 1, page_size: int = 10) -> str:
        """获取当前用户的阅读历史。当用户想看浏览记录、阅读历史时使用。
        参数 page: 页码，默认1。page_size: 每页数量，默认10。"""
        rows, total = await history.get_history_list(db, user.id, page, page_size)
        if not rows:
            return "你还没有浏览记录。"
        items = []
        for news_obj, view_time, hist_id in rows:
            items.append(f"- [{hist_id}] {news_obj.title} (浏览于 {view_time})")
        return f"共 {total} 条历史（第{page}页）：\n" + "\n".join(items)

    @tool
    async def recommend_by_history(limit: int = 5) -> str:
        """基于用户阅读历史推荐文章。当用户想要推荐、猜你喜欢时使用。
        参数 limit: 推荐数量，默认5。"""
        rows, total = await history.get_history_list(db, user.id, 1, 5)
        if not rows:
            return "你还没有浏览记录，无法基于历史推荐。可以先浏览一些新闻。"
        category_ids = set()
        for news_obj, _, _ in rows:
            category_ids.add(news_obj.category_id)
        if not category_ids:
            return "暂无推荐数据。"
        recommendations = []
        for cid in category_ids:
            related = await news.get_related_news(db, rows[0][0].id, cid, limit)
            recommendations.extend(related)
            if len(recommendations) >= limit:
                break
        if not recommendations:
            return "暂无推荐内容。"
        items = []
        for i, n in enumerate(recommendations[:limit], 1):
            items.append(f"{i}. [{n.id}] {n.title} — {n.author or '未知'}")
        return "为你推荐：\n" + "\n".join(items)

    return [search_news, get_news_detail, get_categories, get_favorites, get_history, recommend_by_history]
