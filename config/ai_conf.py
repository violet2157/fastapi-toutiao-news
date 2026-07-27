import os

# ========== LLM 配置 (DeepSeek OpenAI兼容接口) ==========
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-a57be7328ce742aa9dab6d9553a55547")
DASHSCOPE_ENDPOINT = os.getenv("DASHSCOPE_ENDPOINT", "https://api.deepseek.com/v1")
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "deepseek-v4-flash")

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
