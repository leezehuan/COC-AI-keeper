# 【阅读顺序 2：数据库连接与初始化】
# 这个文件负责 SQLAlchemy 数据库引擎、会话工厂和表初始化。
# 对初学者来说，核心概念：
# - engine：数据库连接池，所有 SQL 操作都通过它发送给 PostgreSQL。
# - SessionLocal：数据库会话工厂，每次请求创建一个 db 会话，请求结束后关闭。
# - Base：所有 ORM 模型的基类，models.py 里的每个类都继承它。
# - get_db()：FastAPI 依赖注入函数，用 Depends(get_db) 自动获取和释放数据库会话。
# - init_db()：根据模型定义自动创建所有数据库表（开发时使用，生产用迁移工具）。
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。models.py 中的每个表模型都继承 Base，
    这样 SQLAlchemy 才能知道它们对应数据库中的哪些表。
    """
    pass


# 创建数据库引擎：pool_pre_ping=True 表示每次从连接池取连接时先测试连通性，
# 避免长时间空闲后连接被 PostgreSQL 关闭导致报错。
settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)

# 创建会话工厂：
# - autoflush=False：不自动把内存修改刷到数据库，由代码显式控制
# - autocommit=False：不自动提交事务，必须显式 db.commit()
# - expire_on_commit=False：commit 后对象属性不过期，仍可直接访问
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用的数据库会话生成器。

    用法：在路由函数参数中写 db: Session = Depends(get_db)，
    FastAPI 会在请求进来时自动创建 db 会话，请求结束后自动关闭。
    初学者可以把它理解为"每个 HTTP 请求独享一个数据库连接"。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """根据 ORM 模型定义创建所有数据库表。

    注意：这里 import models 是为了触发模型注册到 Base.metadata 上。
    生产环境建议使用 Alembic 迁移工具，而不是每次启动都 create_all。
    """
    from app import models

    Base.metadata.create_all(bind=engine)
