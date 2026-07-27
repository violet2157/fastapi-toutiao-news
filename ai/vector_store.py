import os

from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.embeddings import get_embeddings
from config.ai_conf import CHUNK_OVERLAP, CHUNK_SIZE, RETRIEVER_TOP_K, VECTOR_STORE_PATH
from models.news import News

COLLECTION_NAME = "news_collection"


def _get_vector_store() -> Chroma:
    os.makedirs(VECTOR_STORE_PATH, exist_ok=True)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=VECTOR_STORE_PATH,
    )


async def rebuild_index(db: AsyncSession) -> int:
    """从 MySQL 读取所有新闻，分块后重建向量索引。返回索引的文档数。"""
    result = await db.execute(select(News))
    news_list = result.scalars().all()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
    )

    docs = []
    for news in news_list:
        text = f"标题：{news.title}\n摘要：{news.description or ''}\n正文：{news.content or ''}"
        chunks = splitter.create_documents(
            texts=[text],
            metadatas=[{
                "news_id": news.id,
                "title": news.title,
                "category_id": news.category_id,
                "author": news.author or "",
            }],
        )
        docs.extend(chunks)

    vector_store = _get_vector_store()
    vector_store.reset_collection()
    if docs:
        vector_store.add_documents(docs)
    return len(docs)


def get_retriever():
    """返回 LangChain Retriever，用于 RAG 链。"""
    return _get_vector_store().as_retriever(search_kwargs={"k": RETRIEVER_TOP_K})


def is_index_ready() -> bool:
    """检查向量库是否已有数据。"""
    try:
        store = _get_vector_store()
        return store._collection.count() > 0
    except Exception:
        return False
