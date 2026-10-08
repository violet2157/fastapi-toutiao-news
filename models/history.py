from datetime import datetime
from sqlalchemy import UniqueConstraint, Index, Integer, ForeignKey, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from models.news import News
from models.users import User


class Base(DeclarativeBase):
    pass
class History(Base):
    """
    浏览历史表表ORM模型
    """
    __tablename__ = 'history'

    # 创建索引
    # UniqueConstraint: 唯一约束, 当前用户，当前新闻，只能出现一次
    __table_args__ = (
        UniqueConstraint('user_id', 'news_id', name='user_news_history_unique'),
        Index('fk_history_user_idx', 'user_id'),
        Index('fk_history_news_idx', 'news_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="历史ID")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey(User.id), nullable=False, comment="用户ID")
    news_id: Mapped[int] = mapped_column(Integer, ForeignKey(News.id), nullable=False, comment="新闻ID")
    view_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, comment="阅读时间")

    def __repr__(self):
        return f"<History(id={self.id}, user_id={self.user_id}, news_id={self.news_id}, view_time={self.view_time})>"