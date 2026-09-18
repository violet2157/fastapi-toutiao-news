import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from ai.agent import resume_agent_response, stream_agent_response
from ai.vector_store import get_retriever, is_index_ready, rebuild_index
from config.db_config import get_db
from models.users import User
from utils.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _sse(item) -> str:
    """把 agent 流里的一项转成 SSE 报文。

    - str            → 普通的模型输出分片
    - {"interrupt":…} → 需要人工审批：前端收到 [INTERRUPT] 后应弹出审批框，
                        再调 /api/ai/approve 继续。
    """
    if isinstance(item, dict) and "interrupt" in item:
        payload = json.dumps(item["interrupt"], ensure_ascii=False)
        return f"data: [INTERRUPT] {payload}\n\n"
    return f"data: {item}\n\n"


@router.post("/chat")
async def ai_chat(
    body: dict,
    user: User = Depends(get_current_user),
):
    messages = body.get("messages", [])
    thread_id = body.get("thread_id", str(user.id))

    if not messages:
        raise HTTPException(status_code=400, detail="messages不能为空")
    if not is_index_ready():
        raise HTTPException(status_code=503, detail="向量索引未就绪，请先调用 /api/ai/rebuild-index")

    async def generate():
        try:
            async for item in stream_agent_response(user, messages, thread_id):
                yield _sse(item)
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/approve")
async def ai_approve(
    body: dict,
    user: User = Depends(get_current_user),
):
    """人工审批：批准/拒绝 agent 暂停下来的工具调用，然后继续流式返回。

    请求体示例：
        {"thread_id": "1", "decision": "approve"}
        {"thread_id": "1", "decision": "reject", "message": "不想看这条"}
    """
    thread_id = body.get("thread_id", str(user.id))
    decision = body.get("decision", "approve")
    message = body.get("message")

    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 只能是 approve 或 reject")

    one: dict = {"type": decision}
    if message:
        one["message"] = message

    async def generate():
        try:
            async for item in resume_agent_response(user, thread_id, [one]):
                yield _sse(item)
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
