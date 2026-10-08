"""用 Playwright 爬取 IT之家 首页科技新闻，写进本项目的 MySQL（news 表）。

为什么要单独爬 IT之家？
    中新网没有文字版「科技」频道：/it/ 频道是空壳，首页的「科技」入口指向的是
    图片频道（channel.chinanews.com.cn/u/pic/kj），滚动新闻 25 页里 it 前缀出现 0 次。
    所以科技分类改从 IT之家 抓——它是国内头部的科技资讯站，文章页结构干净、UTF-8 编码。

和 crawl_news.py 的分工：
    crawl_news.py → 中新网（国内/社会/国际/娱乐/体育/财经）
    crawl_tech.py  → IT之家（科技）
    两个脚本写同一张 news 表、都按标题去重、跑完都清 Redis 缓存。

运行（和 crawl_news.py 一样，建议先 chcp 65001）：
    python crawl_tech.py
"""

import asyncio
import re
import random
import sys
from datetime import datetime

# Windows 下让中文 print 不乱码；line_buffering 让日志实时落盘，方便看进度
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from playwright.async_api import async_playwright
from sqlalchemy import select

from config.db_config import AsyncSessionLocal, async_engine
from config.cache_conf import redis_client
from models.news import Category, News

# ===== 配置 =====
IT_HOME_URL = "https://www.ithome.com/"   # IT之家首页，最新新闻列表就在这页
MAX_ARTICLES = 100                         # 最多入库多少条科技新闻
DELAY = 0.5                                # 每篇之间停几秒，礼貌一点
HEADLESS = True                            # 后台静默跑

# 冷启动阅读数：同 crawl_news.py，新抓的新闻给一个随机初始阅读量，避免全是「0 阅读」。
COLD_START_VIEWS = (100, 2000)

# 反爬伪装（和中新网脚本同一套思路）
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

# IT之家文章详情 URL 形如 /1/003/910.htm（或老格式 /0/795/795.htm），
# 首页导航/栏目链接没有这个三段数字结构，用这个正则精准挑出文章详情页
ARTICLE_RE = re.compile(r"/\d+/\d+/\d+\.htm")


def resolve_url(href: str) -> str:
    """把首页拿到的链接拼成完整 URL（相对链接补上域名）。"""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http"):
        return href
    return "https://www.ithome.com" + href


def parse_publish_time(text: str) -> datetime:
    """解析 IT之家时间「2026/9/18 8:31:55」（月/日/时 不补零）。"""
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{1,2}):(\d{1,2})", text)
    if m:
        return datetime(*map(int, m.groups()))
    return datetime.now()  # 解析不出来就兜底用当前时间


def parse_author(text: str) -> str:
    """从「作者：问舟」里抠出作者名，没有就默认 IT之家。"""
    m = re.search(r"作者[:：]\s*(\S+)", text)
    return m.group(1).strip() if m else "IT之家"


async def fetch_detail(page, url: str) -> dict | None:
    """打开一篇 IT之家详情页，抓出结构化数据。拿不到正文就返回 None。"""
    await page.goto(url, timeout=20_000, wait_until="domcontentloaded")
    await page.wait_for_timeout(300)  # 给 JS 一点渲染时间

    # 标题：详情页唯一的 h1
    title_loc = page.locator("h1")
    if await title_loc.count() == 0:
        return None
    title = (await title_loc.first.inner_text()).strip()

    # 正文：div#paragraph 是 IT之家正文容器（div.content 里会混进面包屑导航，不能用）
    body_loc = page.locator("div#paragraph")
    if await body_loc.count() == 0:
        return None
    content = (await body_loc.first.inner_text()).strip()
    # 去掉开头那句「感谢IT之家网友 xxx 的线索投递！」，让简介/正文更干净
    content = re.sub(r"^感谢IT之家网友.*?线索投递！\s*", "", content)

    # 时间：#pubtime_baidu 存标准格式时间
    pub_text = ""
    pub_loc = page.locator("span#pubtime_baidu")
    if await pub_loc.count():
        pub_text = (await pub_loc.first.inner_text()).strip()
    publish_time = parse_publish_time(pub_text)

    # 作者：#author_baidu 形如「作者：问舟」
    author = "IT之家"
    author_loc = page.locator("span#author_baidu")
    if await author_loc.count():
        author = parse_author((await author_loc.first.inner_text()).strip())

    # 封面图：正文第一张图。IT之家 src 是懒加载占位图，真实地址在 data-original
    image = None
    img_loc = page.locator("div#paragraph img")
    if await img_loc.count():
        src = (
            (await img_loc.first.get_attribute("data-original"))
            or (await img_loc.first.get_attribute("src"))
            or ""
        )
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
    """爬完清掉新闻相关缓存，避免前端列表还显示旧数据。"""
    patterns = ["news_list:*", "news:count:*", "news:detail:*", "news:related:*"]
    try:
        for pattern in patterns:
            keys = [k async for k in redis_client.scan_iter(pattern)]
            if keys:
                await redis_client.delete(*keys)
        print("已清理新闻缓存")
    except Exception as e:
        print(f"清理缓存失败（不影响爬取结果）: {e}")


async def main():
    # ---- 先连一次库，拿到「分类名 → id」映射 和「已有标题」集合 ----
    async with AsyncSessionLocal() as session:
        cats = (await session.execute(select(Category))).scalars().all()
        name_to_id = {c.name: c.id for c in cats}
        existing_titles = set(
            (await session.execute(select(News.title))).scalars().all()
        )

    tech_cat_id = name_to_id.get("科技")
    if tech_cat_id is None:
        print("数据库里没有「科技」分类，请先检查 news_category 表。")
        return

    inserted = 0
    skipped = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(**STEALTH)
        page = await context.new_page()

        # 屏蔽图片/样式/字体等静态资源，纯抓正文能快很多
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2,ico,mp4,mp3}",
            lambda route: route.abort(),
        )

        try:
            # ---- 1. 打开首页，收集文章详情链接 ----
            await page.goto(IT_HOME_URL, timeout=20_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(800)

            links = page.locator("a")
            hrefs = []
            for i in range(await links.count()):
                hrefs.append(await links.nth(i).get_attribute("href"))

            # 去重 + 只留文章详情链接（首页每篇会出现两次：缩略图 + 标题）
            article_urls = []
            seen = set()
            for h in hrefs:
                if not h:
                    continue
                if ARTICLE_RE.search(h):
                    url = resolve_url(h)
                    if url not in seen:
                        seen.add(url)
                        article_urls.append(url)

            print(f"首页共找到 {len(article_urls)} 条科技文章链接")

            # ---- 2. 逐个抓详情并入库 ----
            for url in article_urls:
                if inserted >= MAX_ARTICLES:
                    break

                try:
                    data = await fetch_detail(page, url)
                except Exception as e:
                    print(f"跳过（抓取失败）{url}: {e}")
                    skipped += 1
                    continue

                if data is None or not data["content"]:
                    skipped += 1
                    continue

                # 只收 2026 年的新闻（和 crawl_news.py 保持一致）
                if data["publish_time"].year != 2026:
                    skipped += 1
                    continue

                # 去重：标题已经存在就不重复入库
                if data["title"] in existing_titles:
                    skipped += 1
                    continue

                news = News(
                    title=data["title"],
                    description=data["content"][:200],   # 简介 = 正文前 200 字
                    content=data["content"],
                    image=data["image"],
                    author=data["author"],
                    category_id=tech_cat_id,
                    views=random.randint(*COLD_START_VIEWS),  # 冷启动阅读数
                    publish_time=data["publish_time"],
                )
                async with AsyncSessionLocal() as session:
                    session.add(news)
                    await session.commit()

                existing_titles.add(data["title"])
                inserted += 1
                print(f"[{inserted}] {data['title'][:30]}")

                await asyncio.sleep(DELAY)

        finally:
            await browser.close()

    print(f"\n完成：新增 {inserted} 条科技新闻，跳过 {skipped} 条（含重复/失败/非2026）")
    await clear_news_cache()
    # 不用再补索引：检索走 MySQL 全文索引，INSERT 时数据库自己就维护好了
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
