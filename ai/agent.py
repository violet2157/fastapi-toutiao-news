from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from ai.rag import RAG_SYSTEM_PROMPT, _format_docs
from ai.tools import create_tools
from ai.vector_store import get_retriever
from config.ai_conf import (
    AGENT_MAX_ITERATIONS,
    AGENT_MAX_TOKENS,
    AGENT_TEMPERATURE,
    DASHSCOPE_API_KEY,
    DASHSCOPE_ENDPOINT,
    DASHSCOPE_MODEL,
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


_memory = MemorySaver()


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=DASHSCOPE_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_ENDPOINT,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
    )


async def create_agent(db: AsyncSession, user: User):
    """创建 LangGraph ReAct Agent，注入数据库会话和用户信息。"""
    llm = _build_llm()
    tools = create_tools(db, user)

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=AGENT_SYSTEM_PROMPT,
        checkpointer=_memory,
    )
    return agent


async def stream_agent_response(
    db: AsyncSession,
    user: User,
    messages: list[dict],
    thread_id: str,
):
    """流式执行 Agent，逐 token 返回。

    参数:
        db: 数据库会话
        user: 当前用户
        messages: 对话历史 [{"role":"user","content":"..."}, ...]
        thread_id: 会话线程ID，用于跨轮对话记忆
    """
    agent = await create_agent(db, user)

    config = {"configurable": {"thread_id": thread_id}}

    async for event in agent.astream_events(
        {"messages": messages},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        if kind == "on_chat_model_stream":
            data = event.get("data", {})
            chunk = data.get("chunk", {})
            if hasattr(chunk, "content") and chunk.content:
                yield chunk.content
