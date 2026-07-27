from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from ai.agent import stream_agent_response
from ai.vector_store import get_retriever, is_index_ready, rebuild_index
from config.db_config import get_db
from models.users import User
from utils.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/chat")
async def ai_chat(
    body: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    messages = body.get("messages", [])
    thread_id = body.get("thread_id", str(user.id))

    if not messages:
        raise HTTPException(status_code=400, detail="messages不能为空")
    if not is_index_ready():
        raise HTTPException(status_code=503, detail="向量索引未就绪，请先调用 /api/ai/rebuild-index")

    async def generate():
        try:
            async for token in stream_agent_response(db, user, messages, thread_id):
                yield f"data: {token}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/search")
async def semantic_search(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """纯语义搜索：不经过 LLM，直接返回向量检索结果。"""
    query = body.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query参数不能为空")
    if not is_index_ready():
        raise HTTPException(status_code=503, detail="向量索引未就绪，请先调用 /api/ai/rebuild-index")

    retriever = get_retriever()
    docs = await retriever.ainvoke(query)
    results = [
        {
            "news_id": doc.metadata.get("news_id"),
            "title": doc.metadata.get("title"),
            "content": doc.page_content[:300],
            "score": doc.metadata.get("score", 0),
        }
        for doc in docs
    ]
    return {"code": 200, "data": results, "message": "ok"}


@router.post("/rebuild-index")
async def rebuild_search_index(db: AsyncSession = Depends(get_db)):
    """从 MySQL 重建向量索引（管理接口）。"""
    try:
        count = await rebuild_index(db)
        return {"code": 200, "data": {"indexed_docs": count}, "message": f"成功重建索引，共 {count} 条文档"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"索引重建失败: {str(e)}")


@router.get("/index-status")
async def index_status():
    """检查向量索引状态。"""
    ready = is_index_ready()
    return {"code": 200, "data": {"ready": ready}, "message": "ok"}
