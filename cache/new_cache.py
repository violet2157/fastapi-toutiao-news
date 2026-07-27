#新闻缓存相关方法:新闻分类的读取和写入
from typing import List,Dict,Any,Optional

from config.cache_conf import get_cache, set_cache, get_json_cache

CATEGORIES_KEY = "news:categories"
NEWS_LIST_KEY = "news_list:"
DETAILS_KEY = "news:detail:"
NEWS_COUNT_KEY = "news:count:"
RELATED_NEWS_KEY = "news:related:"
async def get_cache_categories():
    return await get_json_cache(CATEGORIES_KEY)

async def set_cache_categories(data:List[Dict[str, Any]],expire:int = 7200):
    return await set_cache(CATEGORIES_KEY, data, expire)

async def set_cache_news_list(category_id:Optional[int],page:int,size:int,news_list:List[Dict[str, Any]],expire:int = 1800):
    category_part = category_id if category_id is not None else "all"
    key = f"{NEWS_LIST_KEY}{category_part}:{page}:{size}"
    return await set_cache(key, news_list, expire)

async def get_cache_news_list(category_id:Optional[int],page:int,size:int):
    category_part = category_id if category_id is not None else "all"
    key = f"{NEWS_LIST_KEY}{category_part}:{page}:{size}"
    return await get_json_cache(key)

async def set_cache_news_detail(news_id:int,news_detail:Dict[str, Any],expire:int = 600):
    key = f"{DETAILS_KEY}{news_id}"
    return await set_cache(key, news_detail, expire)
async def get_cache_news_detail(news_id: int):
    key = f"{DETAILS_KEY}{news_id}"
    return await get_json_cache(key)

async def get_cache_news_count(category_id: int):
    key = f"{NEWS_COUNT_KEY}{category_id}"
    return await get_json_cache(key)

async def set_cache_news_count(category_id: int, count: int, expire: int = 120):
    key = f"{NEWS_COUNT_KEY}{category_id}"
    return await set_cache(key, count, expire)

async def get_cache_related_news(news_id: int, category_id: int, limit: int):
    key = f"{RELATED_NEWS_KEY}{category_id}:exclude_{news_id}:{limit}"
    return await get_json_cache(key)

async def set_cache_related_news(news_id: int, category_id: int, limit: int, data: list, expire: int = 300):
    key = f"{RELATED_NEWS_KEY}{category_id}:exclude_{news_id}:{limit}"
    return await set_cache(key, data, expire)