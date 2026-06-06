# 通用工具函数：项目根路径解析、路径拼接、安全键名生成。
# 这些函数被多个模块共用，不依赖任何业务逻辑。
from pathlib import Path


def project_root() -> Path:
    """返回项目根目录（即 coc-lite/ 目录）。

    Path(__file__).resolve().parents[2] 表示从当前文件往上 2 层：
    utils.py -> app/ -> backend/ -> coc-lite/
    """
    return Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    """将配置中的相对路径转为绝对路径。如果是绝对路径则直接返回。

    例如："./data/chroma" -> "d:/Project/coc-lite/data/chroma"
    """
    raw = Path(path)
    if raw.is_absolute():
        return raw
    return project_root() / raw


def safe_key(value: str) -> str:
    """将任意字符串转为安全的标识符键名：小写、去斜杠、空格转下划线。

    例如："灯塔地下室" -> "灯塔地下室"
          "a/b c" -> "a_b_c"
    用于线索和物品的幂等去重键（clue_key / item_key）。
    """
    return "_".join(value.strip().lower().replace("/", " ").replace("\\", " ").split())
