from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from sqlalchemy.exc import SQLAlchemyError

from app.api import router
from app.config import get_settings
from app.utils import resolve_project_path

settings = get_settings()
app = FastAPI(title=settings.app_name)


# 统一把数据库异常转换成前端可读的提示，避免泄露底层连接细节。
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "数据库连接失败，请检查 .env 中的 DATABASE_URL、PostgreSQL 服务状态、用户名、密码和数据库名。"},
    )


# 统一处理 LLM 或 Embedding 服务异常，便于前端显示明确的排障方向。
@app.exception_handler(OpenAIError)
async def openai_exception_handler(request: Request, exc: OpenAIError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"detail": "模型服务调用失败，请检查 LLM/Embedding 配置、API Key、模型名称和服务限制。"},
    )


# CORS 配置来自 .env，开发环境通常允许前端 Vite 服务跨域访问。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

assets_dir = resolve_project_path(settings.assets_dir)
if assets_dir.exists():
    # 剧本附件、地图和图片通过 /coc/assets 暴露给前端。
    app.mount("/coc/assets", StaticFiles(directory=str(assets_dir)), name="assets")

# 所有业务 API 统一挂载到 /coc 前缀，和前端 base path 保持一致。
app.include_router(router, prefix="/coc")
