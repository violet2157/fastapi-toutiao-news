# -*- coding: utf-8 -*-
import bcrypt


def get_hash_password(password: str) -> str:
    """
    密码加密：把明文密码变成 bcrypt 哈希后存进数据库（绝不明文存密码）

    bcrypt 工作原理：
    1. gensalt() 随机生成一段"盐值"（salt，默认 cost=12），
       盐值让"同样的密码"每次算出来的哈希都不一样，能防彩虹表攻击
    2. hashpw() 把"盐 + 密码"一起算出最终的哈希
    3. 结果是一串 $2b$12$.... 格式的字符串，盐值就藏在里面，
       所以之后校验密码时不用另外存盐
    """
    # bcrypt 只处理前 72 个字节：超过 72 字节时 bcrypt>=4.1 会直接抛 ValueError，
    # 导致注册接口 500。这里手动截断到 72 字节，避免长密码（尤其中文密码，1 个汉字=3 字节）直接报错。
    password_bytes = password.encode("utf-8")[:72]
    # bcrypt 只接受 bytes，所以先把密码 encode 成字节串
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    # 数据库字段是字符串，而哈希结果是 bytes，需要 decode 回 str
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    密码校验：登录 / 改密码时，拿用户输入的明文和数据库里的哈希比对

    返回 True / False（匹配则 True）
    """
    # checkpw 内部会从 hashed_password 里取出盐值重新算一遍，再比较结果
    # 同样要截断到 72 字节，否则登录/改密码时输入超长密码也会抛 ValueError
    return bcrypt.checkpw(
        plain_password.encode("utf-8")[:72],
        hashed_password.encode("utf-8"),
    )
