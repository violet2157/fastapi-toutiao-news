import os

from dotenv import load_dotenv

load_dotenv()

# ========== LLM 配置 (DeepSeek OpenAI兼容接口) ==========
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_ENDPOINT = os.getenv("DASHSCOPE_ENDPOINT", "https://api.deepseek.com/v1")
# 注意：deepseek-v4-flash 是推理模型，思考走 reasoning_content，message.content 可能为空，
# 要用干净文本输出请用 deepseek-chat
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "deepseek-chat")

# 新闻检索不再用本地 embedding 模型 + Chroma 向量库，改成 MySQL 全文索引
# （FULLTEXT + ngram），见 crud/news.py。所以这里没有 embedding / 分块 / 向量库配置了。

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
#
# 为什么是这几个「删除类」工具？
#   审批的意义在于「有副作用 / 不可逆的动作，先让人确认一下」。
#   之前拿 get_news_detail 当示例其实说不通——它只是只读（顶多浏览量 +1），
#   批准它纯属多余的一步。换成真正会删数据的工具后这个机制才成立：
#   agent 说「我要清空你的收藏」→ 用户点批准 → 才真的删。
# 原则：只给「会改数据」的工具加审批，只读工具（搜索/看详情/看列表）不加，避免无意义打断。
# 真实企业场景里，这里放的是发邮件、下单、改配置、删数据这类高风险工具；
# 更严格的做法还会把审批路由给「另一个角色」（如主管），即职责分离。
HITL_TOOLS = ["clear_history", "remove_favorite", "clear_favorites"]
