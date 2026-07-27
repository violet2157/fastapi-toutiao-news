from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

from ai.vector_store import get_retriever
from config.ai_conf import (
    AGENT_MAX_TOKENS,
    AGENT_TEMPERATURE,
    DASHSCOPE_API_KEY,
    DASHSCOPE_ENDPOINT,
    DASHSCOPE_MODEL,
)

RAG_SYSTEM_PROMPT = """你是新闻助手，用中文回复。你必须严格基于下面提供的数据库新闻来回答问题。

如果数据库新闻无法回答用户的问题，请如实回答"数据库暂无相关信息，无法回答该问题"，不要编造信息。

数据库新闻：
{context}"""


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=DASHSCOPE_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_ENDPOINT,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
    )


def _format_docs(docs) -> str:
    if not docs:
        return "（暂无相关新闻）"
    items = []
    for i, doc in enumerate(docs, 1):
        title = doc.metadata.get("title", "未知标题")
        items.append(f"{i}. 【{title}】\n{doc.page_content}")
    return "\n\n".join(items)


def create_rag_chain():
    """构建 LCEL RAG 链: query → retriever → format → prompt → llm → parser"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM_PROMPT),
        ("human", "{question}"),
    ])
    llm = _build_llm()

    chain = (
        {"context": get_retriever() | _format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain
