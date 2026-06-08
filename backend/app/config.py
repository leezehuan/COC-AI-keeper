# 【阅读顺序 1：项目配置】
# 这个文件定义了整个项目的配置项，所有配置都从 .env 文件读取。
# 对初学者来说，可以把它理解为"项目运行时需要知道的所有参数清单"：
# - 数据库连接地址、LLM API 密钥、向量数据库路径等。
# - 使用 pydantic-settings 库，它会自动从 .env 文件读取同名环境变量覆盖默认值。
# - get_settings() 使用 @lru_cache 确保全局只创建一次配置对象，避免重复读取 .env。
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # pydantic-settings 的核心：指定 .env 文件路径和编码，extra="ignore" 表示忽略 .env 中未定义的变量。
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ===== 基础应用配置 =====
    app_name: str = "克苏鲁守秘人轻量版"  # 应用名称，显示在 FastAPI 文档标题
    app_env: str = "development"  # 运行环境：development / production

    # ===== 数据库配置 =====
    # PostgreSQL 连接串，格式：postgresql+psycopg://用户名:密码@主机:端口/数据库名
    # psycopg 是 PostgreSQL 的异步驱动，+psycopg 表示使用该驱动
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/coc_lite"
    # CORS 允许的前端来源地址，多个用逗号分隔（开发时是 Vite 的 5173 端口）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"

    # ===== LLM（大语言模型）配置 =====
    # 使用 OpenAI 兼容接口，可以接入任何兼容 OpenAI API 格式的模型服务
    llm_base_url: str = ""  # API 基础地址，如 https://api.openai.com/v1 或其他兼容服务地址
    llm_api_key: str = ""  # API 密钥，在 .env 中填写，不要硬编码到代码里
    llm_model: str = ""  # 模型名称，如 gpt-4o、qwen-plus 等
    llm_temperature: float = 0.7  # 生成温度：0 更确定，1 更随机；守秘人需要一定随机性

    # ===== Embedding（文本向量化）配置 =====
    # 用于 RAG 检索：把文本转成向量，以便在向量库中做相似度搜索
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 阿里云千问 Embedding 服务
    embedding_api_key: str = ""  # 千问 API 密钥
    embedding_model: str = "text-embedding-v4"  # 千问文本向量化模型
    embedding_dimensions: int | None = Field(default=1024)  # 向量维度，越高越精确但越慢

    # ===== 图片生成配置 =====
    drawing_api_url: str = "https://api.aabao.top/v1/images/generations"  # 图片生成 API 地址
    drawing_api_key: str = ""  # 图片生成 API 密钥
    drawing_model: str = "doubao-seedream-4-5-251128"  # 图片生成模型名称
    # 图片生成前的场景描述优化，使用另一个 LLM 来润色 prompt
    drawing_llm_base_url: str = ""  # 润色用的 LLM 地址（可与主 LLM 不同）
    drawing_llm_api_key: str = ""  # 润色用的 LLM 密钥
    drawing_llm_model: str = ""  # 润色用的 LLM 模型名

    # ===== 数据与资源路径配置 =====
    # 以下路径相对于项目根目录，resolve_project_path() 会把它们转成绝对路径
    chroma_path: str = "./data/chroma"  # Chroma 向量数据库持久化目录
    scenario_path: str = "./无光的灯塔/无光的灯塔/full.md"  # 剧本全文 Markdown 路径
    rulebook_paths: str = "./keeper-rulebook/主持人规则书.md,./investigator-handbook/full.md"  # 规则书路径，逗号分隔
    character_dir: str = "./无光的灯塔/预设人物卡"  # 预设角色卡目录（.xlsx 文件）
    assets_dir: str = "./无光的灯塔"  # 静态资源目录（地图、附件等），通过 /coc/assets 暴露

    @property
    def cors_origin_list(self) -> list[str]:
        """将逗号分隔的 CORS 来源字符串拆分成列表。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def rulebook_path_list(self) -> list[Path]:
        """将逗号分隔的规则书路径字符串拆分成 Path 列表。"""
        return [Path(path.strip()) for path in self.rulebook_paths.split(",") if path.strip()]


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例。@lru_cache 保证只创建一次，之后每次调用返回同一个对象。

    初学者注意：这是"单例模式"的一种实现——整个进程只需要一份配置。
    """
    return Settings()
