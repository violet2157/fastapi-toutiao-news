from typing import List
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from schemas.base import NewsItemBase


class FavoriteCheckResponse(BaseModel):
    is_favorited: bool = Field(...,alias="isFavorite")

class FavoriteAddRequest(BaseModel):
    news_id: int = Field(...,alias="newsId")

#规划两个类：一个是新闻模型类 + 收藏的模型类
class FavoriteNewsItemResponse(NewsItemBase):
    favorite_id:int = Field(alias="favoriteId")
    favorite_time: datetime = Field(...,alias="favoriteTime")

#收藏列表接口响应模型类
class FavoriteListResponse(BaseModel):
    list:list[FavoriteNewsItemResponse]
    total:int
    has_more:bool = Field(...,alias="hasMore")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True
    )