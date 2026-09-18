from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
import aiosqlite

from ai.tools import create_tools
from config.ai_conf import (
    AGENT_MAX_TOKENS,
    AGENT_TEMPERATURE,
    CHECKPOINT_DB,
    DASHSCOPE_API_KEY,
    DASHSCOPE_ENDPOINT,
    DASHSCOPE_MODEL,
    HITL_TOOLS,
)
from models.users import User

AGENT_SYSTEM_PROMPT = """你是新闻助手，用中文回复。你可以使用工具来搜索新闻、查看收藏、浏览历史等。

工作原则：
1. 当用户问及新闻相关内容时，优先使用 search_news 工具搜索数据库
2. 当用户想看收藏、我的收藏时，使用 get_favorites 工具
3. 当用户想看浏览记录、阅读历史时，使用 get_history 工具
4. 当用户想要推荐时，使用 recommend_by_history 工具
5. 搜索结果中的 [数字] 是新闻ID，用户想看详情时用 get_news_detail 工具
6. 如果工具返回的信息不足以回答，如实告知用户
7. 回答要简洁、结构化，善用列表"""


# ============================================================
# Checkpointer：对话状态的持久化存储
# ------------------------------------------------------------
# 用 AsyncSqliteSaver 把状态落到本地 SQLite 文件（checkpoints.sqlite），
# 服务重启后还能接着之前的对话、甚至接着一个「卡在审批中的」任务继续跑。
#
# 必须是 Async 版：agent 是异步跑的（astream_events），同步 SqliteSaver 的
# aget_tuple 会直接抛 NotImplementedError。
# ============================================================
_checkpointer: AsyncSqliteSaver | None = None
_conn: aiosqlite.Connection | None = None


async def init_checkpointer():
    """启动时调用：连 SQLite、建表、准备好 checkpointer。"""
    global _checkpointer, _conn
    _conn = await aiosqlite.connect(CHECKPOINT_DB)
    _checkpointer = AsyncSqliteSaver(_conn)
    await _checkpointer.setup()  # 建 checkpoints / writes 等表（幂等，重复调用没关系）


async def close_checkpointer():
    """关闭时调用：断开 SQLite 连接，避免资源泄漏。"""
    global _checkpointer, _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
        _checkpointer = None


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=DASHSCOPE_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_ENDPOINT,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
    )


def _build_middleware():
    """构建中间件列表。目前只有人工审批这一条。"""
    if not HITL_TOOLS:
        return []
    # HumanInTheLoopMiddleware：当模型要调用 interrupt_on 里列出的工具时，
    # 在真正执行前先 interrupt（暂停并把「待批动作」抛给前端）。
    # allowed_decisions 限定前端只能批准或拒绝（也可放开 edit/respond）。
    return [
        HumanInTheLoopMiddleware(
            interrupt_on={
                name: {"allowed_decisions": ["approve", "reject"]}
                for name in HITL_TOOLS
            }
        )
    ]


def build_agent(user: User):
    """创建 LangGraph ReAct Agent（带持久化记忆 + 人工审批中间件）。"""
    if _checkpointer is None:
        raise RuntimeError("checkpointer 未初始化，请确认 main.py 的 lifespan 已调用 init_checkpointer()")
    agent = create_agent(
        model=_build_llm(),
        tools=create_tools(user),
        system_prompt=AGENT_SYSTEM_PROMPT,
        checkpointer=_checkpointer,
        middleware=_build_middleware(),
    )
    return agent


async def _pending_interrupt(agent, config) -> dict | None:
    """读取当前 thread 是否有「卡住待审批」的 interrupt。

    agent 被 interrupt 后，run 会正常结束（不报错），暂停点记在 state 里。
    用 aget_state 取出来看看 tasks 里有没有挂起的 interrupt，
    有的话把它的值（含工具名、参数）返回给前端。
    """
    snapshot = await agent.aget_state(config)
    for task in snapshot.tasks:
        for intr in task.interrupts:
            return intr.value
    return None


async def _stream_events(agent, payload, config):
    """把 agent 的事件流翻译成「token 文本」和「interrupt 信号」。

    yield 出来的东西有两种：
        str            —— 一段模型输出文本
        {"interrupt": …} —— 需要人工审批（前端据此弹出审批框）
    """
    async for event in agent.astream_events(payload, config=config, version="v2"):
        if event.get("event") == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk is not None and getattr(chunk, "content", None):
                yield chunk.content

    # 流跑完后检查有没有卡在审批上的 interrupt
    pending = await _pending_interrupt(agent, config)
    if pending is not None:
        yield {"interrupt": pending}


async def stream_agent_response(user: User, messages: list[dict], thread_id: str):
    """发起一轮对话并流式返回。

    参数:
        user: 当前用户
        messages: 对话历史 [{"role":"user","content":"..."}, ...]
        thread_id: 会话线程ID，用于跨轮对话记忆（存进 SQLite）
    """
    agent = build_agent(user)
    config = {"configurable": {"thread_id": thread_id}}
    async for item in _stream_events(agent, {"messages": messages}, config):
        yield item


async def resume_agent_response(
    user: User,
    thread_id: str,
    decisions: list[dict],
):
    """人工审批后恢复被暂停的对话。

    参数:
        user: 当前用户（要和发起对话时是同一个，工具才能拿到正确身份）
        thread_id: 同一个会话线程ID，才能找回暂停点
        decisions: 审批决定，形如 [{"type": "approve"}] 或
                   [{"type": "reject", "message": "理由"}]
    """
    agent = build_agent(user)
    config = {"configurable": {"thread_id": thread_id}}
    # Command(resume=...) 是 LangGraph 的「恢复」指令：
    # 把 decisions 喂回之前 interrupt() 暂停的地方，继续往下跑
    async for item in _stream_events(agent, Command(resume={"decisions": decisions}), config):
        yield item
