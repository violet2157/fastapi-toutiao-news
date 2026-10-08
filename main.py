import sys
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import news, users, favorite, history, ai
from utils.exception_handlers import register_error_handlers


def _warm_up_ai_in_background():
    """在后台线程里 import AI 重依赖。

    AI 那套依赖（langchain / langgraph）光是 import 就要几秒。
    如果放在模块顶层同步 import，整个服务都得等它，新闻接口跟着一起慢。
    改成后台线程预热后：服务几秒内就能对外提供新闻/分类接口，
    AI 依赖在后台慢慢加载，用户点开「AI 问答」时通常已经就绪。
    """
    try:
        import ai.agent  # noqa: F401  触发 langchain 等重依赖的 import
    except Exception as e:
        print(f"AI 依赖预热失败（不影响新闻接口）: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：后台预热 AI 依赖，关闭时释放 checkpointer。"""
    threading.Thread(target=_warm_up_ai_in_background, daemon=True).start()
    yield
    # checkpointer 现在由 ensure_checkpointer() 按需建立；只有 AI 模块真被加载过才有连接要释放
    agent_mod = sys.modules.get("ai.agent")
    if agent_mod is not None:
        await agent_mod.close_checkpointer()


app = FastAPI(lifespan=lifespan)
#注册异常处理器
register_error_handlers(app)
#CORS中间件配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], #允许的源，开发允许所有，生产环境需指定
    allow_credentials=True, #允许携带cookie
    allow_methods=["*"], #允许的请求方法
    allow_headers=["*"], #允许的请求头
)

@app.get("/")
async def root():
    return {"message": "Hello World"}
#挂载路由/注册路由
app.include_router(news.router)
app.include_router(users.router)
app.include_router(favorite.router)
app.include_router(history.router)
app.include_router(ai.router)
