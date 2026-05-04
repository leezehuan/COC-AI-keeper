import argparse

from app.database import SessionLocal, init_db
from app.services.importer import import_default_content


def main() -> None:
    parser = argparse.ArgumentParser(description="克苏鲁守秘人轻量版后端工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-default", help="导入默认剧本、规则书和角色卡")
    import_parser.add_argument("--reset-chroma", action="store_true", help="导入前重置 Chroma 向量库")
    import_parser.add_argument("--skip-characters", action="store_true", help="跳过角色卡导入")

    subparsers.add_parser("init-db", help="创建 PostgreSQL 数据表")

    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
        print("数据库已初始化")
        return

    if args.command == "import-default":
        init_db()
        with SessionLocal() as db:
            result = import_default_content(db, reset_chroma=args.reset_chroma, include_characters=not args.skip_characters)
        print(
            "导入完成："
            f"剧本编号 {result['scenario_id']}，"
            f"剧本分块 {result['scenario_chunks']}，"
            f"规则分块 {result['rule_chunks']}，"
            f"角色 {result['characters']} 个"
        )
        return


if __name__ == "__main__":
    main()
