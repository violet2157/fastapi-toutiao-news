import os

from dotenv import load_dotenv

load_dotenv()

# ========== LLM 配置 (DeepSeek OpenAI兼容接口) ==========
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_ENDPOINT = os.getenv("DASHSCOPE_ENDPOINT", "https://api.deepseek.com/v1")
# 注意：deepseek-v4-flash 是推理模型，思考走 reasoning_content，message.content 可能为空，
# 要用干净文本输出请用 deepseek-chat
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "deepseek-chat")

# ========== Embedding 模型配置 (本地模型，支持中文) ==========
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

# ========== 向量库配置 ==========
VECTOR_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")

# ========== 文档分块参数 ==========
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# ========== RAG 检索参数 ==========
RETRIEVER_TOP_K = 5

# ========== Agent 配置 ==========
AGENT_MAX_ITERATIONS = 6
AGENT_TEMPERATURE = 0.7
AGENT_MAX_TOKENS = 1024

# ========== Checkpointer 配置 ==========
# 对话状态的持久化文件。用 SQLite 落盘，服务重启后同一 thread_id 的记忆还在。
# （之前用的 MemorySaver 只存在内存里，一重启对话记忆就没了）
CHECKPOINT_DB = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "checkpoints.sqlite"
)

# ========== 人工审批 (Human-in-the-Loop) 配置 ==========
# 需要「人工审批」才能执行的工具名列表。agent 调这些工具前会暂停，
# 等前端调 /api/ai/approve 批准后才真正执行。
# 真实企业场景里，这里放的是有副作用/高风险的工具（发邮件、下单、删数据…）；
# 本项目工具只读为主，拿 get_news_detail（读全文+累加浏览量）作演示。
HITL_TOOLS = ["get_news_detail"]
