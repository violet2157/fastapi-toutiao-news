import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from config.ai_conf import DASHSCOPE_API_KEY, DASHSCOPE_ENDPOINT, DASHSCOPE_MODEL
from config.db_config import get_db
from crud.news import search_news
from models.users import User
from utils.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


def build_context(articles) -> str:
    if not articles:
        return ""
    items = []
    for i, n in enumerate(articles, 1):
        desc = n.description or (n.content[:100] if n.content else "")
        items.append(f"{i}. 【{n.title}】{desc}")
    return "\n".join(items)


@router.post("/chat")
async def ai_chat(
    body: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    messages = body.get("messages", [])
    user_question = messages[-1]["content"] if messages else ""

    # RAG检索：从数据库搜索相关新闻
    related_articles = await search_news(db, user_question)
    context = build_context(related_articles)

    system_content = "你是新闻助手，用中文回复。"
    if context:
        system_content += f"\n\n以下数据库中与用户问题相关的新闻，请优先基于这些新闻回答：\n{context}\n\n你只能基于以下数据库新闻回答。如果数据库新闻无法回答问题，请如实告知用户\"数据库暂无相关信息\"。"

    payload = {
        "model": DASHSCOPE_MODEL,
        "messages": [
            {"role": "system", "content": system_content},
            *messages,
        ],
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 1024,
        "top_p": 0.9,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
    }

    async def generate():
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", DASHSCOPE_ENDPOINT, json=payload, headers=headers) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")
