"""临时脚本：删除并重置 turn_logs 表以适配新的图片字段。
运行方式：python drop_turn_logs.py
"""
from sqlalchemy import create_engine, text
from backend.app.config import get_settings

settings = get_settings()
database_url = settings.database_url

engine = create_engine(database_url)
with engine.connect() as conn:
    with conn.begin():
        conn.execute(text("DROP TABLE IF EXISTS turn_logs CASCADE"))
        print("已执行 DROP TABLE turn_logs CASCADE")
        conn.execute(text("DROP TABLE IF EXISTS clues CASCADE"))
        print("已执行 DROP TABLE clues CASCADE")
        conn.execute(text("DROP TABLE IF EXISTS inventory_items CASCADE"))
        print("已执行 DROP TABLE inventory_items CASCADE")
        conn.execute(text("DROP TABLE IF EXISTS story_flags CASCADE"))
        print("已执行 DROP TABLE story_flags CASCADE")
        conn.execute(text("DROP TABLE IF EXISTS sessions CASCADE"))
        print("已执行 DROP TABLE sessions CASCADE")
print("所有相关表已清理，下次启动 FastAPI 时 SQLAlchemy 会自动重建表结构。")
