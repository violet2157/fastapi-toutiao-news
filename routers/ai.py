import asyncio
import importlib
import json
import sys

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from config.db_config import get_db
from crud import news as news_crud
from models.users import User
from utils.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _load_ai_agent():
    """同步 import ai.agent（供 asyncio.to_thread 在线程里调用）。"""
    return importlib.import_module("ai.agent")


async def _ai_agent():
    """惰性拿到 ai.agent 模块。

    本路由不在模块顶层 import ai.agent —— 那会连带把 langchain / langgraph
    一起拉进来，服务启动要等十几秒。第一次真正调用 AI 接口时才 import，
    且放进线程里做，避免卡住事件循环；之后模块已在 sys.modules 里，直接取用，秒回。

    判据为什么是「有没有 stream_agent_response 属性」而不是「在不在 sys.modules」？
        Python 是「先把模块塞进 sys.modules、再执行模块体」。后台预热线程刚开始
        import ai.agent 时，模块已经在 sys.modules 里、但里面还是半成品，
        这时按「在不在 sys.modules」判断会误以为已就绪，取属性就 AttributeError。
        而 import_module 遇到别的线程正在 import 同一模块时会在模块锁上等，
        等它执行完才返回，所以走这一条路拿到的永远是完整模块。
    """
    agent_mod = sys.modules.get("ai.agent")
    if agent_mod is not None and hasattr(agent_mod, "stream_agent_response"):
        return agent_mod
    return await asyncio.to_thread(_load_ai_agent)


def _sse(item) -> str:
    """把 agent 流里的一项转成 SSE 报文。

    - str             → 普通的模型输出分片
    - {"interrupt":…} → 需要人工审批：前端收到 [INTERRUPT] 后应弹出审批框，
                        再调 /api/ai/approve 继续。
    - {"tool":…}      → 工具开始执行（前端显示工具调用提示）
    - {"tool_end":…}  → 工具执行结束
    """
    if isinstance(item, dict):
        if "interrupt" in item:
            payload = json.dumps(item["interrupt"], ensure_ascii=False)
            return f"data: [INTERRUPT] {payload}\n\n"
        if "tool" in item:
            payload = json.dumps(item["tool"], ensure_ascii=False)
            return f"data: [TOOL] {payload}\n\n"
        if "tool_end" in item:
            payload = json.dumps(item["tool_end"], ensure_ascii=False)
            return f"data: [TOOL_END] {payload}\n\n"
    # 文本分片也用 json 编码：模型输出里可能带换行，直接拼 "data: {item}" 会让
    # 一条 SSE 事件被拆成多行，前端按行解析时会丢内容。json 编码后保证是一行。
    return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


def _scoped_thread_id(user: User, raw_thread_id) -> str:
    """把前端传来的 thread_id 收进「当前用户」的命名空间。

    为什么必须这么做：
        checkpointer 是拿 thread_id 当 key 存对话状态的。如果直接把 body 里的
        thread_id 拿去查，A 用户只要传 B 的 thread_id，就能让 agent 带着 B 的
        历史消息跑，B 搜过的新闻、收藏内容会出现在 A 的输出里（越权读取）。
        approve 接口同理，还能恢复别人卡住的审批。

    做法：
        服务端强制加上 "u{user.id}:" 前缀。客户端传什么值都只能落在自己的
        命名空间内，且无法伪造出别人的前缀（前缀由服务端拼，不由客户端提供），
        跨用户访问从根上不可能。前端仍可自定义 thread_id 来区分同一个人开的
        多个会话，只是它永远带不上别人的前缀。
    """
    raw = (raw_thread_id or "").strip() if isinstance(raw_thread_id, str) else ""
    # 前端没传 / 传了空串时给个固定默认值：同一用户不传就落在同一个默认会话上
    return f"u{user.id}:{raw or 'default'}"


@router.post("/chat")
async def ai_chat(
    body: dict,
    user: User = Depends(get_current_user),
):
    agent_mod = await _ai_agent()
    messages = body.get("messages", [])
    thread_id = _scoped_thread_id(user, body.get("thread_id"))

    if not messages:
        raise HTTPException(status_code=400, detail="messages不能为空")

    async def generate():
        try:
            async for item in agent_mod.stream_agent_response(user, messages, thread_id):
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
    agent_mod = await _ai_agent()
    thread_id = _scoped_thread_id(user, body.get("thread_id"))
    decision = body.get("decision", "approve")
    message = body.get("message")

    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 只能是 approve 或 reject")

    one: dict = {"type": decision}
    if message:
        one["message"] = message

    async def generate():
        try:
            async for item in agent_mod.resume_agent_response(user, thread_id, [one]):
                yield _sse(item)
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/search")
async def search(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """纯关键词搜索：不经过 LLM，直接返回数据库检索结果（调试/联调用）。"""
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query参数不能为空")

    rows = await news_crud.search_news(db, query, limit=body.get("limit", 5))
    results = [
        {
            "news_id": n.id,
            "title": n.title,
            "content": (n.description or n.content or "")[:300],
            "score": score,
        }
        for n, score in rows
    ]
    return {"code": 200, "data": results, "message": "ok"}
