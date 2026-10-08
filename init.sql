-- ============================================================
-- 新闻资讯 App —— 建库建表脚本
-- ------------------------------------------------------------
-- 用途：本项目没有用 SQLAlchemy 的 create_all 自动建表，表需要手工创建。
--       新环境初始化时执行本文件即可。
--
-- 执行方式：
--   mysql -u root -p < init.sql
--   或用 Navicat / DataGrip 打开本文件直接运行
--
-- 说明：本文件是手工整理的等价 SQL，**字段的唯一事实来源是 models/ 下的
--       SQLAlchemy 模型**。改模型时记得同步这里。
-- ============================================================

CREATE DATABASE IF NOT EXISTS news_app
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE news_app;

-- ------------------------------------------------------------
-- 1. 新闻分类
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_category (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '分类id',
    name       VARCHAR(50)  NOT NULL COMMENT '分类名称',
    sort_order INT          NOT NULL DEFAULT 0 COMMENT '排序',
    -- 以下两列来自 models/news.py 里 Base 提供的公共字段
    created_at DATETIME     NULL COMMENT '创建时间',
    updated_at DATETIME     NULL COMMENT '修改时间',
    UNIQUE KEY name_UNIQUE (name)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT '新闻分类表';

-- ------------------------------------------------------------
-- 2. 新闻主表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news (
    id           INT AUTO_INCREMENT PRIMARY KEY COMMENT '新闻ID',
    title        VARCHAR(255) NOT NULL COMMENT '新闻标题',
    description  VARCHAR(500) NULL COMMENT '新闻简介',
    content      TEXT         NOT NULL COMMENT '新闻内容',
    image        VARCHAR(255) NULL COMMENT '封面图片URL',
    author       VARCHAR(50)  NULL COMMENT '作者',
    category_id  INT          NOT NULL COMMENT '分类ID',
    views        INT          NOT NULL DEFAULT 0 COMMENT '浏览量',
    publish_time DATETIME     NULL COMMENT '发布时间',
    created_at   DATETIME     NULL COMMENT '创建时间',
    updated_at   DATETIME     NULL COMMENT '修改时间',

    -- 高频查询列上的索引（对应 models/news.py 的 __table_args__）
    KEY fk_news_category_idx (category_id),
    KEY idxpublish_time (publish_time),
    CONSTRAINT fk_news_category FOREIGN KEY (category_id) REFERENCES news_category (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT '新闻表';

-- ------------------------------------------------------------
-- 3. 全文索引（AI 助手检索用）
-- ------------------------------------------------------------
-- 必须带 WITH PARSER ngram：MySQL 默认按空格/标点分词，中文整句切不出词，
-- 索引会是空的。ngram 改成按固定字数滑窗切（ngram_token_size 默认 2）。
--
-- 注：crud/news.py 的 ensure_fulltext_index() 会在首次搜索时自动检查并补建
--     这个索引（漏建也不会搜不到），这里写出来是为了让新环境一步到位。
--     若这行报「ngram parser 不存在」，说明该 MySQL 未启用 ngram，代码会自动
--     退化为 LIKE 模糊匹配，功能仍可用。
ALTER TABLE news
    ADD FULLTEXT INDEX ft_news_search (title, description, content) WITH PARSER ngram;

-- ------------------------------------------------------------
-- 4. 用户表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `user` (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
    username   VARCHAR(50)  NOT NULL COMMENT '用户名',
    password   VARCHAR(255) NOT NULL COMMENT '密码（bcrypt 哈希，绝不明文存储）',
    nickname   VARCHAR(50)  NULL COMMENT '昵称',
    avatar     VARCHAR(255) NULL DEFAULT 'https://fastly.jsdelivr.net/npm/@vant/assets/cat.jpeg' COMMENT '头像URL',
    gender     ENUM ('male','female','unknown') NULL DEFAULT 'unknown' COMMENT '性别',
    bio        VARCHAR(500) NULL DEFAULT '这个人很懒，什么都没留下' COMMENT '个人简介',
    phone      VARCHAR(20)  NULL COMMENT '手机号',
    created_at DATETIME     NULL COMMENT '创建时间',
    updated_at DATETIME     NULL COMMENT '更新时间',
    UNIQUE KEY username_UNIQUE (username),
    UNIQUE KEY phone_UNIQUE (phone)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT '用户表';

-- ------------------------------------------------------------
-- 5. 登录令牌表
-- ------------------------------------------------------------
-- 本项目用的是「不透明令牌存库」而不是 JWT：前端在 Authorization 头里带
-- Bearer <token>，后端拿 token 查这张表换出用户（见 utils/auth.py）。
CREATE TABLE IF NOT EXISTS user_token (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '令牌ID',
    user_id    INT          NOT NULL COMMENT '用户ID',
    token      VARCHAR(255) NOT NULL COMMENT '令牌值',
    expires_at DATETIME     NOT NULL COMMENT '过期时间',
    created_at DATETIME     NULL COMMENT '创建时间',
    UNIQUE KEY token_UNIQUE (token),
    KEY fk_user_token_user_idx (user_id),
    CONSTRAINT fk_user_token_user FOREIGN KEY (user_id) REFERENCES `user` (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT '用户令牌表';

-- ------------------------------------------------------------
-- 6. 收藏表
-- ------------------------------------------------------------
-- (user_id, news_id) 唯一：同一用户对同一条新闻只能收藏一次，
-- 靠数据库约束兜住并发下的重复插入。
CREATE TABLE IF NOT EXISTS favorite (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '收藏ID',
    user_id    INT      NOT NULL COMMENT '用户ID',
    news_id    INT      NOT NULL COMMENT '新闻ID',
    created_at DATETIME NOT NULL COMMENT '收藏时间',
    UNIQUE KEY user_news_unique (user_id, news_id),
    KEY fk_favorite_user_idx (user_id),
    KEY fk_favorite_news_idx (news_id),
    CONSTRAINT fk_favorite_user FOREIGN KEY (user_id) REFERENCES `user` (id),
    CONSTRAINT fk_favorite_news FOREIGN KEY (news_id) REFERENCES news (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT '收藏表';

-- ------------------------------------------------------------
-- 7. 浏览历史表
-- ------------------------------------------------------------
-- 同样是 (user_id, news_id) 唯一：同一条新闻重复浏览只更新时间，不堆记录。
CREATE TABLE IF NOT EXISTS history (
    id        INT AUTO_INCREMENT PRIMARY KEY COMMENT '历史ID',
    user_id   INT      NOT NULL COMMENT '用户ID',
    news_id   INT      NOT NULL COMMENT '新闻ID',
    view_time DATETIME NOT NULL COMMENT '阅读时间',
    UNIQUE KEY user_news_history_unique (user_id, news_id),
    KEY fk_history_user_idx (user_id),
    KEY fk_history_news_idx (news_id),
    CONSTRAINT fk_history_user FOREIGN KEY (user_id) REFERENCES `user` (id),
    CONSTRAINT fk_history_news FOREIGN KEY (news_id) REFERENCES news (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT '浏览历史表';
