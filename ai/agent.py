import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, wrap_model_call
from langchain_core.messages import AIMessage, ToolMessage
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

AGENT_SYSTEM_PROMPT = """你是「新闻资讯」App 的中文新闻助手，只处理新闻相关需求。

【语言】全程简体中文，包括调用工具前的过渡文字，不要先说一句英文。
无中文译名的专有名词（iPhone、AI 等）可保留原文。

【范围】只回答新闻相关问题（找新闻、看详情、收藏、浏览记录、推荐等）。
与新闻无关的请求（写代码、翻译、闲聊、通用知识或数学题等），只回一句「我只负责新闻相关的问题」，
并把话题引回新闻，不要展开回答。
工具返回的信息不足以回答时如实说明，不要编造。

【怎么选工具】
- 找新闻、问某个话题 → search_news；工具结果里的 news_id 是你调 get_news_detail 时要用的内部字段
- 想看收藏 → get_favorites；想看浏览记录 → get_history；要推荐 → recommend_by_history
- 取消收藏某条 / 清空收藏 / 清空浏览记录等删除类操作 → 调用对应工具。
  调用前不要输出任何文字，也不用追问「确定吗」：系统会自动弹出确认框让用户批准

【输出要求】
1. 需要调用工具时，不要输出任何文字，直接调用工具（界面会显示工具状态），
   等工具返回结果后再给结论。理由：调用前那句过渡话不仅没用，还容易变成英文
2. 给用户看的列表一律用**自然序号 1. 2. 3. …**，每条一行（标题 + 一句简介）
3. **绝对不要把 news_id 这类内部字段展示给用户**（不要出现 [915]、(news_id=915) 这类写法），
   它只供你自己调工具时对照。用户说「展开第 2 条」时，你自己回到工具结果里对上号
4. 简洁：不复述用户的问题、不重复工具返回的原文、不写客套话，能一句话说清就不写三句"""


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
_init_lock = asyncio.Lock()


async def init_checkpointer():
    """连 SQLite、建表、准备好 checkpointer。"""
    global _checkpointer, _conn
    _conn = await aiosqlite.connect(CHECKPOINT_DB)
    _checkpointer = AsyncSqliteSaver(_conn)
    await _checkpointer.setup()  # 建 checkpoints / writes 等表（幂等，重复调用没关系）


async def ensure_checkpointer():
    """按需初始化 checkpointer（幂等）。

    本模块连同 langchain / langgraph 这些重依赖是「懒加载」的：服务启动时
    main.py 只在后台线程里预热 import。所以第一次真正用到 AI 时在这里补建
    checkpointer，已经建过就直接返回。
    加锁是为了防止多个请求同时进来、重复建连接。
    """
    if _checkpointer is not None:
        return
    async with _init_lock:
        if _checkpointer is None:  # 双检：拿到锁后再确认一次
            await init_checkpointer()


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


# ============================================================
# 中间件：清理「悬空的 tool_calls」，防止 400 错误
# ------------------------------------------------------------
# 背景：DeepSeek 这类 OpenAI 兼容接口有个硬性校验——一条带 tool_calls 的
# assistant 消息，后面必须紧跟对应的 tool 结果消息（ToolMessage），否则报
# 400：「An assistant message with 'tool_calls' must be followed by tool messages」。
#
# 正常流程里这个顺序不会破：模型发起工具调用 → 工具执行 → ToolMessage 写回。
# 但如果 checkpointer（SQLite 对话记忆）里存了脏数据（某个工具调用没拿到结果
# 就留在了历史里），下次对话把这整段历史再发给模型，就会触发 400。与其让用户
# 手动删库，不如在每次调用模型前自动扫一遍历史、把没有结果的 tool_calls 摘掉，
# 让这个错误从根上不可能发生。
# ============================================================
@wrap_model_call
async def sanitize_dangling_tool_calls(request, handler):
    """模型调用前，把「没有对应工具结果」的 tool_calls 从历史消息里移除。

    遍历 request.messages，对每条带 tool_calls 的 AIMessage：
      只保留「后续消息里出现过同名 tool_call_id 的 ToolMessage」的 tool_call，
      其余视为悬空（脏数据），直接删掉。删完如果有变化，就把清洗后的消息列表
      通过 request.override(messages=...) 传回，再继续调模型。
    """
    messages = request.messages
    changed = False
    cleaned: list = []

    for i, msg in enumerate(messages):
        # 只看「带工具调用」的 AI 消息
        if isinstance(msg, AIMessage) and msg.tool_calls:
            # 收集这条消息「后面」已经拿到结果的 tool_call_id
            answered_ids = {
                m.tool_call_id
                for m in messages[i + 1:]
                if isinstance(m, ToolMessage) and m.tool_call_id
            }
            # 只保留有结果的那部分 tool_calls
            kept = [tc for tc in msg.tool_calls if tc.get("id") in answered_ids]
            if len(kept) != len(msg.tool_calls):
                # model_copy 生成一条新消息，不动原来的（避免污染 checkpoint 里的原始数据）
                msg = msg.model_copy(update={"tool_calls": kept})
                changed = True
        cleaned.append(msg)

    if changed:
        return await handler(request.override(messages=cleaned))
    return await handler(request)


def _build_middleware():
    """构建中间件列表：先清洗脏历史，再做人工审批。"""
    middleware = [sanitize_dangling_tool_calls]
    if not HITL_TOOLS:
        return middleware
    # HumanInTheLoopMiddleware：当模型要调用 interrupt_on 里列出的工具时，
    # 在真正执行前先 interrupt（暂停并把「待批动作」抛给前端）。
    # allowed_decisions 限定前端只能批准或拒绝（也可放开 edit/respond）。
    middleware.append(
        HumanInTheLoopMiddleware(
            interrupt_on={
                name: {"allowed_decisions": ["approve", "reject"]}
                for name in HITL_TOOLS
            }
        )
    )
    return middleware


def build_agent(user: User):
    """创建 LangGraph ReAct Agent（带持久化记忆 + 人工审批中间件）。"""
    if _checkpointer is None:
        raise RuntimeError("checkpointer 未初始化，请先 await ensure_checkpointer()")
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
    """把 agent 的事件流翻译成前端能用的几种信号。

    yield 出来的东西有四种：
        str               —— 一段模型输出文本
        {"interrupt": …}  —— 需要人工审批（前端据此弹出审批框）
        {"tool": …}       —— agent 开始调用某个工具（前端显示「正在搜索新闻…」）
        {"tool_end": …}   —— 某个工具执行完毕
    """
    async for event in agent.astream_events(payload, config=config, version="v2"):
        kind = event.get("event")
        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk is not None and getattr(chunk, "content", None):
                yield chunk.content
        elif kind == "on_tool_start":
            # 工具开始执行：把工具名和入参推给前端做「工具调用可视化」。
            # 注意：被人工审批拦下的工具（如 clear_favorites）会先走 interrupt、
            # 批准后才真正执行，所以它的 on_tool_start 会出现在批准之后。
            yield {"tool": {"name": event.get("name"), "input": event.get("data", {}).get("input")}}
        elif kind == "on_tool_end":
            yield {"tool_end": {"name": event.get("name")}}

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
    await ensure_checkpointer()  # 懒加载后由这里保证 checkpointer 已就绪
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
    await ensure_checkpointer()  # 懒加载后由这里保证 checkpointer 已就绪
    agent = build_agent(user)
    config = {"configurable": {"thread_id": thread_id}}
    # Command(resume=...) 是 LangGraph 的「恢复」指令：
    # 把 decisions 喂回之前 interrupt() 暂停的地方，继续往下跑
    async for item in _stream_events(agent, Command(resume={"decisions": decisions}), config):
        yield item
