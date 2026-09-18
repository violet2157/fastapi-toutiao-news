import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker,AsyncSession,create_async_engine

load_dotenv()

#数据库URL（从环境变量读取，避免密钥硬编码）
ASYNC_DATABASE_URL = os.getenv("DATABASE_URL")
if not ASYNC_DATABASE_URL:
    raise RuntimeError("未配置环境变量 DATABASE_URL，请复制 .env.example 为 .env 并填写")
#创建异步引擎
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=True, #输出sql日志
    pool_size=10, #可连接
    max_overflow=20, #可额外链接
    )
#创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
#依赖项，用于获取数据库会话
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()