"""用 Playwright 爬取中国新闻网滚动新闻，写进本项目的 MySQL（news 表）。

这个脚本做的事：
    1. 打开滚动新闻列表页，拿到一批新闻详情页链接
    2. 逐个打开详情页，抓 标题/正文/发布时间/来源/封面图
    3. 按「链接前缀 + 文本关键词预测」双重判断属于哪个分类（国内/社会/国际/娱乐/体育/科技/财经）
    4. 去重后写入 MySQL 的 news 表（复用项目已有的异步 SQLAlchemy 模型）

和之前 demo 的区别：这里把「爬虫」和「数据库」接起来了，正是 RPA/数据开发岗
日常会写的「采集 → 入库」完整链路。面试可以讲三点：
    - 异步：Playwright 用 async API，SQLAlchemy 用 async，全链路不阻塞
    - 去重：插入前先查一遍已有标题，重复运行不会产生脏数据
    - 数据清洗：发布时间从「2026年09月16日 14:50」解析成标准 datetime

运行前准备：
    pip install playwright
    playwright install chromium
    # 且 .env 里配好了 DATABASE_URL（指向 news_app 库）

运行（建议先 chcp 65001，避免中文打印乱码）：
    python crawl_news.py
"""

import asyncio
import re
import random
import sys
from datetime import datetime

# Windows 下让中文 print 不乱码（终端按 GBK 解码时也能正常显示）。
# line_buffering=True：输出重定向到文件时默认是「块缓冲」，print 要攒够一块才落盘，
# 日志看起来会"卡住"，加这个让每行立即写出，方便实时看进度。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from playwright.async_api import async_playwright
from sqlalchemy import select

from config.db_config import AsyncSessionLocal, async_engine
from config.cache_conf import redis_client
from models.news import Category, News

# ===== 配置 =====
LIST_PAGE_URL = "https://www.chinanews.com.cn/scroll-news/news{page}.html"  # 滚动新闻列表页（翻页模板）
BASE = "https://www.chinanews.com.cn"   # 用于把相对链接拼成完整 URL
MAX_ARTICLES = 200                      # 最多入库多少条
MAX_PAGES = 50                          # 最多翻多少页列表（每页十几条有效链接，凑够 200 条）
DELAY = 0.8                             # 每篇详情之间停几秒（别把服务器打挂）
HEADLESS = True                         # 爬数据用后台静默跑，无需弹窗

# 冷启动阅读数：新抓的新闻给一个随机初始阅读量（100~2000），
# 避免列表里全是「0 阅读」显得没人看。真实推荐系统也有类似的「冷启动」兜底思路。
COLD_START_VIEWS = (100, 2000)

# 反爬伪装：UA / 语言 / 时区，降低被识别成机器人的概率
STEALTH = {
    "viewport": {"width": 1366, "height": 768},
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "locale": "zh-CN",
    "timezone_id": "Asia/Shanghai",
    "extra_http_headers": {"Accept-Language": "zh-CN,zh;q=0.9"},
}

# 链接前缀 → 分类名。中新网的详情页 URL 形如 /gn/2026/09-16/xxx.shtml，
# 第一段就是分类缩写。只采集这几个已知分类，视频/图片/军事等跳过。
#
# 两个特殊情况（2026-09 实测）：
#   - 「娱乐」文字新闻现在挂在「文娱」频道，URL 前缀是 cul（/cul/2026/...），
#     老的 yl 前缀已经失效（/yl/ 直接 403），所以把 cul 也归到娱乐。
#   - 「科技」中新网已经没有了文字频道：/it/ 频道是空壳，滚动新闻里 it 前缀
#     25 页出现 0 次，首页「科技」入口指向的是图片频道。it 保留在表里但抓不到内容。
PREFIX_TO_CATEGORY = {
    "gn": "国内",
    "sh": "社会",
    "gj": "国际",
    "yl": "娱乐",   # 老前缀，已失效，保留以防个别旧稿
    "cul": "娱乐",  # 文娱频道（文化/娱乐）用的前缀，归到「娱乐」分类
    "ty": "体育",
    "it": "科技",   # 中新网已无文字版科技频道，抓不到内容
    "cj": "财经",
}

# 文本分类的关键词词典：每个分类配一组「这类新闻高频、别的类少见的」特征词。
# 作用：URL 前缀不认识时（新频道/空频道/没收录的前缀），用标题+正文命中哪些关键词
# 来猜分类，作为前缀分类的兜底/辅助信号。这是「规则式文本分类」，不用训练、不下载
# 模型、离线秒出，面试可讲：先用规则兜底，再谈升级成模型。
CATEGORY_KEYWORDS = {
    "国内": [
        "国务院", "外交部", "商务部", "发改委", "全国人大", "政协", "中央", "习近平",
        "省委", "省政府", "市委书记", "市长", "政府", "政策", "立法", "规划", "部委",
        "改革开放", "十四五", "两会", "国台办",
    ],
    "社会": [
        "警方", "民警", "公安", "法院", "检察院", "判决", "案件", "嫌疑人", "犯罪",
        "事故", "消防", "救援", "受伤", "身亡", "死亡", "男子", "女子", "司机",
        "纠纷", "维权", "曝光", "通报", "抓获", "处罚", "罚款", "醉驾", "诈骗",
        "快递", "外卖", "小区", "物业", "失踪",
    ],
    "国际": [
        "美国", "俄罗斯", "乌克兰", "欧盟", "联合国", "白宫", "特朗普", "普京",
        "总统", "外长", "外国", "国际", "多国", "全球", "中东", "朝鲜", "韩国",
        "英国", "法国", "德国", "加拿大", "澳大利亚", "日本", "印度", "制裁",
        "冲突", "战争", "谈判", "峰会", "首脑", "使馆", "移民", "难民",
    ],
    "娱乐": [
        "演员", "明星", "电影", "电视剧", "综艺", "歌手", "演唱会", "导演", "票房",
        "娱乐圈", "新歌", "音乐", "偶像", "粉丝", "剧组", "主演", "上映", "艺人",
    ],
    "体育": [
        "比赛", "冠军", "球队", "足球", "篮球", "运动员", "教练", "联赛", "世界杯",
        "奥运会", "决赛", "比分", "夺冠", "进球", "赛季", "选手", "裁判", "半决赛",
        "晋级", "淘汰", "点球", "网球", "羽毛球", "乒乓球", "马拉松",
    ],
    "科技": [
        "科技", "手机", "芯片", "人工智能", "互联网", "软件", "硬件", "机器人", "5G",
        "航天", "卫星", "火箭", "研发", "华为", "苹果", "微软", "腾讯", "字节", "小米",
        "AI", "智能",
    ],
    "财经": [
        "经济", "股市", "股票", "基金", "金融", "银行", "央行", "人民币", "汇率",
        "投资", "楼市", "房价", "财报", "营收", "净利润", "上市", "股价", "A股",
        "贸易", "出口", "进口", "融资", "市值",
    ],
}


def resolve_url(href: str) -> str:
    """把列表页拿到的相对链接拼成完整 URL。

    三种情况：
        /gn/xxx.shtml        → 前缀拼 BASE
        //cdn.xxx.com/a.png  → 拼协议 https:
        https://xxx          → 本来就是完整的，直接返回
    """
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http"):
        return href
    return BASE + href


def parse_publish_time(text: str) -> datetime:
    """把发布时间字符串解析成 datetime 对象。

    中新网详情页有两个时间来源：
        #pubtime_baidu      → "2026-09-16 14:50:04"（标准格式，优先）
        .content_left_time  → "2026年09月16日 14:50　来源：中国新闻网"
    这里两种都能解析。
    """
    # 先试标准格式 "2026-09-16 14:50:04"
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})", text)
    if m:
        return datetime(*map(int, m.groups()))
    # 再试中文格式 "2026年09月16日 14:50"
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{1,2})", text)
    if m:
        return datetime(*map(int, m.groups()))
    return datetime.now()  # 实在解析不出来就用当前时间兜底


def parse_source(text: str) -> str:
    """从「2026年09月16日 14:50　来源：中国新闻网」里抠出来源名。"""
    m = re.search(r"来源[:：]\s*(\S+)", text)
    return m.group(1).strip() if m else "中国新闻网"


def predict_category(title: str, content: str = "") -> str | None:
    """用关键词给「标题 + 正文开头」打分，预测分类名；拿不准返回 None。

    规则式文本分类的思路：
        每类有一组特征词（见 CATEGORY_KEYWORDS），统计文本命中哪类词最多。
        标题信息密度最高，正文只取前 300 字就够（正文太长，全算既慢又容易稀释信号）。

    为什么「拿不准就返回 None」？
        这是辅助分类，宁缺毋滥：最高分必须 ≥1，且和次高分打平（歧义）就不猜，
        把不确定的交给「跳过」，避免把一个新闻硬塞进错误的分类。
    """
    text = title + " " + content[:300]
    scores = {cat: 0 for cat in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            scores[cat] += text.count(kw)

    # 按分数从高到低排，比较第一名和第二名
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_cat, top_score = ranked[0]
    second_score = ranked[1][1]
    if top_score >= 1 and top_score > second_score:
        return top_cat
    return None


def resolve_category(prefix: str, title: str, content: str = "") -> str | None:
    """综合「URL 前缀 + 文本关键词」判断新闻分类，拿不准返回 None。

    优先级：
      1. 前缀命中 → 直接用前缀分类。中新网的 URL 频道结构（/gn/ 国内、/gj/ 国际…）
         是站方自己维护的强信号，可信度远高于关键词，所以优先信它。
      2. 前缀不认识 → 用关键词预测兜底，覆盖「新频道 / 空频道 / 没收录的前缀」。
    """
    cat = PREFIX_TO_CATEGORY.get(prefix)
    if cat:
        return cat
    return predict_category(title, content)


async def fetch_detail(page, url: str) -> dict | None:
    """打开一篇详情页，抓出结构化数据。失败返回 None（由上层跳过）。

    返回：{"title","content","publish_time","author","image"}，拿不到正文就返回 None。
    """
    # wait_until="domcontentloaded"：等 DOM 解析完就继续，不用等图片/广告全加载完，
    # 新闻正文是服务端直接渲染的，DOM 一出来就有，这样能快一大截
    await page.goto(url, timeout=20_000, wait_until="domcontentloaded")
    await page.wait_for_timeout(500)  # 给 JS 一点渲染时间

    # 标题：优先 .content h1（列表里 h1 有时会重复出现，取第一个）
    # 注意：async API 里 locator.count() 也是协程，必须 await
    title_loc = page.locator(".content h1")
    if await title_loc.count() == 0:
        return None
    title = (await title_loc.first.inner_text()).strip()

    # 正文：div.left_zw 下的每个 <p> 是一段，拼起来（\n 分隔）
    body_loc = page.locator("div.left_zw p")
    if await body_loc.count() == 0:
        return None
    paras = await body_loc.all_inner_texts()
    content = "\n".join(p.strip() for p in paras if p.strip())

    # 时间 + 来源：优先读 #pubtime_baidu（标准格式），没有就用 .content_left_time
    meta_text = ""
    pub_loc = page.locator("#pubtime_baidu")
    if await pub_loc.count():
        meta_text = (await pub_loc.first.inner_text()).strip()
    else:
        meta_loc = page.locator(".content_left_time")
        if await meta_loc.count():
            meta_text = (await meta_loc.first.inner_text()).strip()

    publish_time = parse_publish_time(meta_text)
    author = parse_source(meta_text)

    # 封面图：正文里的第一张图。注意 base64 占位图不算
    image = None
    img_loc = page.locator("div.left_zw img")
    if await img_loc.count():
        src = (await img_loc.first.get_attribute("src")) or ""
        if src and not src.startswith("data:"):
            image = resolve_url(src)

    return {
        "title": title,
        "content": content,
        "publish_time": publish_time,
        "author": author,
        "image": image,
    }


async def clear_news_cache():
    """爬完清掉新闻相关缓存，避免前端列表还显示旧数据。

    新闻列表在 Redis 里缓存 30 分钟，详情缓存 10 分钟。爬虫直接写库、不经过接口，
    如果不主动清缓存，前端列表会继续显示爬之前的老新闻（甚至已被删除的）。
    """
    patterns = ["news_list:*", "news:count:*", "news:detail:*", "news:related:*"]
    try:
        for pattern in patterns:
            keys = [k async for k in redis_client.scan_iter(pattern)]
            if keys:
                await redis_client.delete(*keys)
        print("已清理新闻缓存")
    except Exception as e:
        # Redis 挂了也不影响爬取结果，只影响缓存，这里降级处理
        print(f"清理缓存失败（不影响爬取结果）: {e}")


async def main():
    # ---- 先连一次库，拿到「分类名 → id」映射 和「已有标题」集合 ----
    async with AsyncSessionLocal() as session:
        cats = (await session.execute(select(Category))).scalars().all()
        name_to_id = {c.name: c.id for c in cats}
        existing_titles = set(
            (await session.execute(select(News.title))).scalars().all()
        )

    # 头条分类的 id（没有对应前缀的兜底）；其实下面只采已知分类，这里仅作兜底
    default_cat_id = name_to_id.get("头条", 1)

    inserted = 0
    skipped = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        # 注意 async API 里 new_context / new_page 都是协程，必须 await
        context = await browser.new_context(**STEALTH)
        page = await context.new_page()

        # 屏蔽图片/样式/字体/视频等静态资源，纯抓正文时能快很多（正文是 HTML 里就有的）
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2,ico,mp4,mp3}",
            lambda route: route.abort(),
        )

        try:
            # ---- 1. 翻页遍历滚动新闻列表，凑够 MAX_ARTICLES 条有效新闻 ----
            for page_no in range(1, MAX_PAGES + 1):
                if inserted >= MAX_ARTICLES:
                    break

                list_url = LIST_PAGE_URL.format(page=page_no)
                try:
                    await page.goto(list_url, timeout=20_000, wait_until="domcontentloaded")
                    await page.locator("div.content_list li a").first.wait_for(timeout=15_000)
                except Exception as e:
                    print(f"列表页打开失败 {list_url}: {e}")
                    continue

                links = page.locator("div.content_list li a")
                # 把列表里每条链接的「href + 标题文字」都取出来：
                # 标题在列表页就能看到，用来「抓详情前先去重」，重复跑时能跳过已知新闻
                items = []
                for i in range(await links.count()):
                    a = links.nth(i)
                    href = await a.get_attribute("href")
                    title_text = (await a.inner_text()).strip()
                    items.append((href, title_text))

                # ---- 2. 逐个处理详情页 ----
                for href, list_title in items:
                    if inserted >= MAX_ARTICLES:
                        break
                    if not href:
                        continue

                    # 文章详情页形如 /gn/2026/09-16/xxx.shtml —— 第二段是 4 位年份。
                    # 用它把「同前缀但不是文章」的链接排掉，尤其是视频页
                    # （如 /sh/shipin/cns-d/2026/...）：这类页面没有正文，抓了返回 None 白费，
                    # 还经常 20 秒超时，会把整个爬取拖得极慢。
                    m = re.match(r"/([a-z]+)/(\d{4})/", href)
                    if not m:
                        continue
                    prefix = m.group(1)
                    prefix_known = prefix in PREFIX_TO_CATEGORY
                    cat_name = resolve_category(prefix, list_title)
                    if cat_name is None or cat_name not in name_to_id:
                        continue

                    # 去重前置：列表页标题已经在库里，就连详情页都不用抓（二次运行秒过）
                    if list_title and list_title in existing_titles:
                        skipped += 1
                        continue

                    detail_url = resolve_url(href)
                    try:
                        data = await fetch_detail(page, detail_url)
                    except Exception as e:
                        # 单篇失败不影响整体，记一下跳过继续下一篇
                        print(f"跳过（抓取失败）{detail_url}: {e}")
                        skipped += 1
                        continue

                    if data is None or not data["content"]:
                        skipped += 1
                        continue

                    # 只收 2026 年的新闻（本任务：爬 26 年新闻，删 26 年以前的）
                    if data["publish_time"].year != 2026:
                        skipped += 1
                        continue

                    # 去重：标题已经存在就不重复入库
                    if data["title"] in existing_titles:
                        skipped += 1
                        continue

                    # 前缀不认识、刚才靠标题关键词猜的分类，抓完正文后再用「标题+正文」
                    # 复核一次：正文比标题长、关键词命中更充分，结果更准。复核拿不准就沿用原结果。
                    if not prefix_known:
                        refined = predict_category(data["title"], data["content"])
                        if refined is not None and refined in name_to_id:
                            cat_name = refined

                    # ---- 3. 写入数据库 ----
                    news = News(
                        title=data["title"],
                        description=data["content"][:200],   # 简介 = 正文前 200 字
                        content=data["content"],
                        image=data["image"],
                        author=data["author"],
                        category_id=name_to_id[cat_name],
                        views=random.randint(*COLD_START_VIEWS),  # 冷启动阅读数
                        publish_time=data["publish_time"],
                    )
                    async with AsyncSessionLocal() as session:
                        session.add(news)
                        await session.commit()

                    existing_titles.add(data["title"])  # 内存里也记一下，避免同批重复
                    inserted += 1
                    print(f"[{inserted}] {cat_name} | {data['title'][:30]}")

                    await asyncio.sleep(DELAY)  # 礼貌一点，别连续打

        finally:
            await browser.close()

    print(f"\n完成：新增 {inserted} 条，跳过 {skipped} 条（含重复/失败/非目标分类）")
    await clear_news_cache()  # 爬完清缓存，前端立刻能看到最新列表
    # 不用再补索引：检索走 MySQL 全文索引，INSERT 时数据库自己就维护好了
    # 显式关闭连接池，避免 asyncio 事件循环关闭后 aiomysql 报 deallocator 警告
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
