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


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "数据库连接失败，请检查 .env 中的 DATABASE_URL、PostgreSQL 服务状态、用户名、密码和数据库名。"},
    )


@app.exception_handler(OpenAIError)
async def openai_exception_handler(request: Request, exc: OpenAIError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"detail": "模型服务调用失败，请检查 LLM/Embedding 配置、API Key、模型名称和服务限制。"},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

assets_dir = resolve_project_path(settings.assets_dir)
if assets_dir.exists():
    app.mount("/coc/assets", StaticFiles(directory=str(assets_dir)), name="assets")

app.include_router(router, prefix="/coc")
