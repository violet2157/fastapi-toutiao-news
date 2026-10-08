from langchain_core.tools import tool

from config.db_config import AsyncSessionLocal
from crud import favorite, history, news
from models.users import User


def create_tools(user: User):
    """创建 Agent 工具集。

    这里只注入 user，不注入请求里的 db session：每个工具自己用
    AsyncSessionLocal() 开一个短连接，用完就关。

    原因：带 checkpointer + 人工审批时，一次对话会被拆成多个 HTTP 请求
    （发起对话 → agent 暂停等审批 → 前端批准 → 恢复执行）。工具若攥着
    「发起对话」那个请求的 session，审批回来时它早被关掉了，恢复执行必然报错。
    各自开 session 才能真正跨请求复用。
    """
    @tool
    async def search_news(query: str) -> str:
        """搜索新闻。当用户想查找某类新闻、询问某个话题相关的新闻时使用。
        参数 query: 搜索关键词或问题描述。"""
        async with AsyncSessionLocal() as db:
            rows = await news.search_news(db, query, limit=5)
        if not rows:
            return "未找到相关新闻。"
        items = []
        for i, (n, _score) in enumerate(rows, 1):
            # 编号用自然序号（1. 2. 3.），news_id 标成「内部字段」：
            # 它是给模型调 get_news_detail 用的，不该原样展示给用户
            desc = (n.description or "").strip()
            items.append(f"{i}. {n.title} (news_id={n.id})\n   {desc[:200]}...")
        return "\n\n".join(items)

    @tool
    async def get_news_detail(news_id: int) -> str:
        """获取指定新闻的详细内容。当用户想看某条新闻的完整内容时使用。
        参数 news_id: 新闻ID。"""
        async with AsyncSessionLocal() as db:
            detail = await news.get_news_detail(db, news_id)
            if not detail:
                return f"新闻ID {news_id} 不存在。"
            await news.increase_news_views(db, news_id)
            await db.commit()  # 自己开的 session 要自己提交
            return (
                f"标题：{detail.title}\n"
                f"作者：{detail.author or '未知'}\n"
                f"发布时间：{detail.publish_time}\n"
                f"内容：{detail.content}"
            )

    @tool
    async def get_categories() -> str:
        """获取所有新闻分类列表。当用户想了解有哪些新闻分类时使用。"""
        async with AsyncSessionLocal() as db:
            cats = await news.get_categories(db)
        if not cats:
            return "暂无分类。"
        return "\n".join(f"- {c.name} (ID:{c.id})" for c in cats)

    @tool
    async def get_favorites(page: int = 1, page_size: int = 10) -> str:
        """获取当前用户的收藏列表。当用户想看自己的收藏、我的收藏时使用。
        参数 page: 页码，默认1。page_size: 每页数量，默认10。"""
        async with AsyncSessionLocal() as db:
            rows, total = await favorite.get_favorite_list(db, user.id, page, page_size)
        if not rows:
            return "你还没有收藏任何新闻。"
        items = []
        for news_obj, fav_time, _fav_id in rows:
            # 注意要暴露的是「新闻ID」而不是收藏表ID：
            # remove_favorite 接收的是 news_id，给收藏表ID会删错/删不掉
            items.append(f"- {news_obj.title} (收藏于 {fav_time}) (news_id={news_obj.id})")
        return f"共 {total} 条收藏（第{page}页）：\n" + "\n".join(items)

    @tool
    async def get_history(page: int = 1, page_size: int = 10) -> str:
        """获取当前用户的阅读历史。当用户想看浏览记录、阅读历史时使用。
        参数 page: 页码，默认1。page_size: 每页数量，默认10。"""
        async with AsyncSessionLocal() as db:
            rows, total = await history.get_history_list(db, user.id, page, page_size)
        if not rows:
            return "你还没有浏览记录。"
        items = []
        for news_obj, view_time, _hist_id in rows:
            # 同样暴露新闻ID（后续推荐/看详情都用得到）
            items.append(f"- {news_obj.title} (浏览于 {view_time}) (news_id={news_obj.id})")
        return f"共 {total} 条历史（第{page}页）：\n" + "\n".join(items)

    @tool
    async def recommend_by_history(limit: int = 5) -> str:
        """基于用户阅读历史推荐文章。当用户想要推荐、猜你喜欢时使用。
        参数 limit: 推荐数量，默认5。"""
        async with AsyncSessionLocal() as db:
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
                items.append(f"{i}. {n.title} — {n.author or '未知'} (news_id={n.id})")
            return "为你推荐：\n" + "\n".join(items)

    @tool
    async def clear_history() -> str:
        """清空当前用户的全部浏览记录。这是一个不可恢复的删除操作。
        只有当用户明确要求「清空 / 删除浏览历史」时才调用。"""
        # 这个工具被配成了需要人工审批（见 config/ai_conf.py 的 HITL_TOOLS）：
        # agent 调它会先 interrupt 暂停，等用户在界面上点「批准」才真正执行删除。
        # 这样「删数据」这种不可逆动作就不会在用户不知情的情况下发生。
        async with AsyncSessionLocal() as db:
            count = await history.remove_all_history(db, user.id)
        if count:
            return f"已清空该用户的全部浏览记录，共删除 {count} 条。"
        return "该用户本来就没有浏览记录，无需清空。"

    @tool
    async def remove_favorite(news_id: int) -> str:
        """取消收藏指定的一条新闻。当用户想取消收藏、不再收藏某条新闻时使用。
        参数 news_id: 要取消收藏的新闻ID（可以从收藏列表或搜索结果里的 [数字] 拿到）。"""
        # 破坏性操作：和 clear_history 一样会走人工审批，用户批准后才真正删除
        async with AsyncSessionLocal() as db:
            ok = await favorite.remove_news_favorite(db, user.id, news_id)
        if ok:
            return f"已取消收藏新闻 {news_id}。"
        return f"新闻 {news_id} 本来就不在收藏里，无需取消。"

    @tool
    async def clear_favorites() -> str:
        """清空当前用户的全部收藏。这是一个不可恢复的删除操作。
        只有当用户明确要求「清空 / 删除全部收藏」时才调用。"""
        # 破坏性操作：会走人工审批
        async with AsyncSessionLocal() as db:
            count = await favorite.remove_all_favorite(db, user.id)
        if count:
            return f"已清空该用户的全部收藏，共删除 {count} 条。"
        return "该用户本来就没有收藏，无需清空。"

    return [
        search_news,
        get_news_detail,
        get_categories,
        get_favorites,
        get_history,
        recommend_by_history,
        clear_history,
        remove_favorite,
        clear_favorites,
    ]
