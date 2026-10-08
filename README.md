# 新闻资讯 App —— 后端服务与智能新闻助手

基于 FastAPI 的新闻资讯后端服务，共 **20 个 RESTful 接口**，覆盖新闻、分类、收藏、浏览历史、AI 助手五大模块。

核心亮点是其中的 **AI 智能助手**：一个带工具调用、人工审批、对话状态持久化与流式输出的 LangGraph ReAct Agent —— 这部分不只是「调用 SDK」，而是围绕 Agent 运行时要解决的工程问题展开（详见[下文](#ai-智能助手设计)）。

---

## 技术栈

| 层次 | 选型 |
| --- | --- |
| Web 框架 | FastAPI + Uvicorn |
| ORM / 数据库 | SQLAlchemy 2.0（异步）、MySQL 8（FULLTEXT + ngram） |
| 缓存 | Redis（分类 / 列表 / 详情缓存） |
| 鉴权 | Bearer Token（令牌落库，非 JWT）+ bcrypt 密码哈希 |
| AI | LangChain + LangGraph、DeepSeek（OpenAI 兼容接口） |
| 对话持久化 | AsyncSqliteSaver（SQLite checkpointer） |
| 数据采集 | Playwright（异步） |

---

## 快速开始

### 1. 环境依赖

- Python 3.11+（本项目开发环境为 3.14）
- MySQL 8.0+（需支持 `ngram` 全文解析器，官方镜像默认支持）
- Redis 6+

### 2. 安装依赖

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

然后编辑 `.env` 填上真实值（见[环境变量](#环境变量)）。

### 4. 建库建表

```bash
mysql -u root -p < init.sql
```

> `init.sql` 会创建 `news_app` 库、6 张表、以及 `news` 表的 ngram 全文索引。
> 项目没有用 `create_all` 自动建表，这一步是必须的。

### 5. 启动

```bash
python -m uvicorn main:app --reload --port 8000
```

### 6. 打开接口文档

- Swagger UI：<http://127.0.0.1:8000/docs>
- ReDoc：<http://127.0.0.1:8000/redoc>

**这里就是本项目最好的演示入口** —— 20 个接口都可在 Swagger UI 上直接填参数、点执行、看响应，无需前端页面。

需要登录的接口（收藏 / 历史 / AI）请先调 `/api/user/register` 拿到 token，再点右上角 **Authorize** 填入 `Bearer <token>`。

### 7. 灌数据（可选）

库里没有新闻时接口会返回空列表，可以用仓库自带的爬虫抓一批：

```bash
python crawl_tech.py    # 抓 IT 之家
python crawl_news.py    # 抓中新网滚动新闻
```

---

## 环境变量

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `DATABASE_URL` | 异步数据库连接串（**必填**，缺失会直接启动失败） | `mysql+aiomysql://root:pwd@localhost:3306/news_app?charset=utf8mb4` |
| `DASHSCOPE_API_KEY` | DeepSeek API Key | `sk-...` |
| `DASHSCOPE_ENDPOINT` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `DASHSCOPE_MODEL` | 模型名 | `deepseek-chat` |

> ⚠️ 模型必须用 `deepseek-chat`。`deepseek-v4-flash` 是推理模型，思考过程走 `reasoning_content` 字段，
> `message.content` 可能为空，会导致助手只输出空白。
>
> Redis 当前是**硬编码**在 `config/cache_conf.py` 里的 `localhost:6379` db0；
> 若你的 Redis 不在此地址，需要改该文件。缓存读写失败不阻塞业务（内部已捕获异常）。

---

## 项目结构

```
├── main.py              # 应用入口：注册路由/异常处理器/CORS，后台线程预热 AI 依赖
├── init.sql             # 建库建表 + 全文索引
├── crawl_news.py        # 中新网滚动新闻爬虫
├── crawl_tech.py        # IT 之家爬虫
├── ai/
│   ├── agent.py         # LangGraph Agent：中间件、checkpointer、事件流翻译
│   └── tools.py         # 9 个业务工具（Agent 可调用）
├── routers/             # 路由层：只做参数校验与响应组装
├── crud/                # 数据访问层：SQL 都写在这里
│   └── news_cache.py    # 新闻模块的 Redis 缓存包装（缓存未命中再落库）
├── models/              # SQLAlchemy ORM 模型
├── schemas/             # Pydantic 请求/响应模型（camelCase 别名对外）
├── config/              # 数据库 / 缓存 / AI 配置
└── utils/               # 鉴权、密码哈希、统一响应、异常处理
```

分层约定：**路由层不写 SQL，CRUD 层不碰 HTTP**。缓存作为 CRUD 之上的包装层（`news_cache`），
让业务代码感知不到缓存的存在。

---

## 接口清单（20 个）

### 新闻 `routers/news.py`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/news/categories` | 分类列表 |
| GET | `/api/news/list` | 新闻列表，参数 `categoryId`（必填）、`page`、`pageSize` |
| GET | `/api/news/detail` | 详情，参数 `id`；同时浏览量 +1 并返回相关推荐 |

> `categoryId=1` 是「头条」，作为聚合推荐位，不按分类过滤，返回全库最新。

### 用户 `routers/users.py`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/user/register` | 注册，成功直接返回 token |
| POST | `/api/user/login` | 登录 |
| GET | `/api/user/info` | 当前用户信息 |
| PUT | `/api/user/update` | 修改昵称/头像/简介等 |
| PUT | `/api/user/password` | 修改密码 |

> 除注册和登录外均需 `Authorization: Bearer <token>`。

### 收藏 `routers/favorite.py`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/favorite/check` | 是否已收藏，参数 `newsId` |
| POST | `/api/favorite/add` | 添加收藏 |
| GET | `/api/favorite/list` | 收藏列表（分页） |
| DELETE | `/api/favorite/remove` | 取消收藏，参数 `newsId` |
| DELETE | `/api/favorite/clear` | 清空收藏 |

### 浏览历史 `routers/history.py`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/history/add` | 记录浏览 |
| GET | `/api/history/list` | 历史列表（分页） |
| DELETE | `/api/history/delete/{history_id}` | 删除单条 |
| DELETE | `/api/history/clear` | 清空历史 |

> 重复浏览同一条新闻只更新 `view_time`，不会堆出多条记录（`add_news_history` 先查后写）。

### AI 助手 `routers/ai.py`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/ai/chat` | 发起对话，SSE 流式返回 |
| POST | `/api/ai/approve` | 人工审批后恢复执行，SSE 流式返回 |
| POST | `/api/ai/search` | 纯关键词检索（不过 LLM，用于调试/联调） |

---

## AI 智能助手设计

这一节是本项目的主要投入所在。

### 整体形态

一个 LangGraph ReAct Agent：模型自主决定调用哪个工具、调用几次，以「不再请求工具」作为唯一退出条件。
对外通过 SSE 流式返回，支持多轮对话记忆与人工审批中断。

### 1. 工具集（9 个）

工具全部定义在 `ai/tools.py`，通过 docstring 与参数 Schema 引导模型选型：

| 类别 | 工具 | 是否需审批 |
| --- | --- | --- |
| 检索 | `search_news`、`get_news_detail`、`get_categories` | 否（只读） |
| 用户数据 | `get_favorites`、`get_history`、`recommend_by_history` | 否（只读） |
| 删除类 | `clear_history`、`remove_favorite`、`clear_favorites` | **是** |

**工具自己开短生命周期数据库会话**，而不是复用请求会话 —— 这是被「审批」逼出来的设计，
原因见下节。

### 2. 人工审批（Human-in-the-Loop）

不可逆操作不能悄悄发生。做法是把 3 个删除类工具登记到 `config/ai_conf.py` 的 `HITL_TOOLS`，
配合 `HumanInTheLoopMiddleware`：模型一旦要调用它们，就在**真正执行前** interrupt 暂停，
把「待批动作」推给前端，用户点批准/拒绝后再恢复。

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as 前端
    participant A as Agent
    participant D as 数据库

    U->>F: "把我的收藏清空"
    F->>A: POST /api/ai/chat
    A-->>F: SSE [TOOL] clear_favorites（准备调用）
    A-->>F: SSE [INTERRUPT] 待审批
    Note over A: 状态存入 SQLite，本轮请求正常结束
    F-->>U: 弹出审批框
    U->>F: 点「批准」
    F->>A: POST /api/ai/approve
    A->>D: 真正执行 DELETE
    A-->>F: SSE 文本结果 + [DONE]
```

几个关键点：

- **为什么必须让工具自己开 session？** 加了 checkpointer + 审批后，一次对话被拆成
  「发起 → 暂停 → 批准 → 恢复」**多个 HTTP 请求**。如果工具攥着发起请求的那个 db session，
  等审批回来时它早被关掉了，恢复执行必然报错。让工具各自 `AsyncSessionLocal()` 开短连接，
  状态才能真正跨请求复用。
- **审批只能给「会改数据」的工具加。** 只读工具（搜索、看详情、看列表）加审批属于无意义打断 ——
  用户每看一条新闻都要点一次确认，体验会崩。
- **更严格的做法**是把审批路由给另一个角色（如主管），即职责分离。本项目简化为同一用户自批。

### 3. 对话状态持久化

用 `AsyncSqliteSaver` 把对话状态落盘到 `checkpoints.sqlite`，而不是内存版 `MemorySaver`：
服务重启后同一 `thread_id` 的记忆还在，**甚至能接着一个「卡在审批中」的任务继续跑**。

两个坑值得记：

- **必须是 async 版。** Agent 是异步跑的（`astream_events`），同步 `SqliteSaver` 的
  `aget_tuple` 会直接抛 `NotImplementedError`。
- **懒加载后需要按需初始化。** 由于 AI 依赖改为懒加载（见第 6 节），checkpointer 不能再在
  lifespan 里建立，改由 `ensure_checkpointer()` 在首次真正用到 AI 时补建，并用 asyncio 锁 +
  双重检查防止并发请求重复建连接。

### 4. 清洗「悬空的 tool_calls」

DeepSeek 这类 OpenAI 兼容接口有硬性校验：一条带 `tool_calls` 的 assistant 消息，
后面必须紧跟对应的工具结果，否则报 400。

正常流程不会破坏这个顺序，但如果 checkpointer 里存了历史遗留脏数据（例如某次工具调用卡住、
没拿到结果），下次把整段历史发给模型就会触发 400。

处理方式是一个 `@wrap_model_call` 中间件（`sanitize_dangling_tool_calls`）：**每次调用模型前**
扫描历史消息，把「后续没有对应工具结果」的 `tool_calls` 摘掉，让这个错误从根上不可能发生，
而不是让用户手动删库。清洗时用 `model_copy` 生成新消息，不污染 checkpoint 里的原始数据。

### 5. SSE 流式协议

`/api/ai/chat` 与 `/api/ai/approve` 返回 `text/event-stream`，把 Agent 的事件流翻译成 4 类信号：

| 前缀 | 含义 | 前端动作 |
| --- | --- | --- |
| `data: "文本"` | 模型输出分片 | 追加到气泡 |
| `data: [TOOL] {...}` | 工具开始执行 | 显示「正在搜索新闻…」 |
| `data: [TOOL_END] {...}` | 工具执行完毕 | 收起工具提示 |
| `data: [INTERRUPT] {...}` | 需要人工审批 | 弹出审批框 |
| `data: [DONE]` / `[ERROR]` | 本轮结束 / 出错 | 结束 loading |

文本分片也走 JSON 编码：模型输出里可能带换行，直接拼 `data: {文本}` 会让一条 SSE 事件被拆成
多行，前端按行解析时会丢内容。JSON 编码后保证一条事件占一行。

### 6. 重依赖懒加载 + 后台预热

LangChain / LangGraph 光 `import` 就要数秒。处理方式分两处：

- `main.py` 在 lifespan 里起一个**后台线程**预热 import，服务本身不被阻塞 ——
  新闻类接口几秒内即可用，AI 依赖在后台慢慢加载；
- `routers/ai.py` **不在模块顶层** import `ai.agent`，首次真正调用 AI 接口时才 import，
  且放进线程里执行，避免卡住事件循环。

这里有个细节：判断模块是否就绪用的是 `hasattr(mod, "stream_agent_response")` 而不是
`"ai.agent" in sys.modules`。因为 Python 是「先把模块塞进 `sys.modules`、再执行模块体」——
预热线程刚开始 import 时模块已在 `sys.modules` 里但仍是半成品，用它判断会误判为已就绪、
取属性直接 `AttributeError`。走 `importlib.import_module` 则会在模块锁上等待，
拿到的永远是完整模块。

### 7. Prompt 设计

系统提示词（`ai/agent.py` 的 `AGENT_SYSTEM_PROMPT`）里有三条值得说的约束：

- **限定业务范围**：只答新闻相关问题，无关提问（写代码、翻译、闲聊）只回一句并引回新闻。
- **调工具前不输出文字**：否则模型容易先冒一句英文过渡话；直接调用工具，界面会显示工具状态。
- **绝不把 `news_id` 展示给用户**：它是模型自己调 `get_news_detail` 用的内部字段。
  用户说「展开第 2 条」时，模型回到工具结果里自己对上号。

---

## 新闻检索设计

AI 助手的 `search_news` 走**全文索引 + LIKE 兜底**两条路（`crud/news.py`）：

1. **MySQL FULLTEXT + ngram**（`MATCH ... AGAINST`）—— 由 MySQL 计算相关度分数，
   标题命中比正文命中分高，排序比 LIKE 更合理。
2. **LIKE 兜底** —— 两个场景下必须用：① 全文索引不可用（环境未启用 ngram）；
   ② 全文索引搜不出结果。后者很常见：ngram 按 2 字切词，搜「车」这种单字切不出词，
   索引必然返回空，而 LIKE 能按字面捞回来。

**`ensure_fulltext_index()` 会自动补建索引**，漏建也不会导致搜不到。查/建失败时返回 `False`
而非抛异常，调用方自动退化为 LIKE，检索功能始终可用。

> 关于「为什么不用 RAG」：新闻是短文本且强时效，向量检索的语义泛化在这里收益有限，
> 反而多出一套 embedding 模型 + 向量库的运维成本，属于负优化。只有「跨多篇语义聚合」
> 或「按意思检索私有语料」才值得上向量检索。本项目据此选择了全文索引。

---

## 数据采集

两个 Playwright 异步爬虫，采集后直接写入 MySQL：

| 脚本 | 来源 | 单次上限 |
| --- | --- | --- |
| `crawl_news.py` | 中新网滚动新闻 | `MAX_ARTICLES = 200`（最多翻 50 页列表） |
| `crawl_tech.py` | IT 之家首页 | `MAX_ARTICLES = 100` |

共同设计：

- **分类判定**：链接前缀 + 文本关键词双重策略。中新网覆盖国内 / 社会 / 国际 / 娱乐 / 体育 / 财经；
  科技单独交给 IT 之家脚本 —— 中新网已无文字版科技频道（`/it/` 是空壳，滚动新闻 25 页里 `it` 前缀出现 0 次）。
- **数据清洗**：把「2026年09月16日 14:50」这类中文时间解析为标准 `datetime`。
- **去重**：入库前按标题查重，脚本可重复运行不产生脏数据。
- **反爬**：UA / 时区伪装 + 请求间隔控制。
- **冷启动**：随机初始阅读量，避免所有卡片显示 0 次浏览。
- **缓存一致性**：采集完成后清理 Redis 缓存，保证读到的是最新数据。

---

## 已知限制

- Redis 地址硬编码在 `config/cache_conf.py`，未走环境变量。
- CORS 放开了 `allow_origins=["*"]`，生产环境需收紧为具体域名。
- 未提供 Dockerfile / docker-compose，部署需自行准备 MySQL 与 Redis。
- `config/db_config.py` 打开了 `echo=True`，SQL 日志全量输出，生产环境建议关闭。
- 爬虫依赖页面结构，目标站点改版后需要同步调整选择器。
