from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "克苏鲁守秘人轻量版"
    app_env: str = "development"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/coc_lite"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.7

    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int | None = Field(default=1024)

    chroma_path: str = "./data/chroma"
    scenario_path: str = "./无光的灯塔/无光的灯塔/full.md"
    rulebook_paths: str = "./keeper-rulebook/主持人规则书.md,./investigator-handbook/full.md"
    character_dir: str = "./无光的灯塔/预设人物卡"
    assets_dir: str = "./无光的灯塔"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def rulebook_path_list(self) -> list[Path]:
        return [Path(path.strip()) for path in self.rulebook_paths.split(",") if path.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
