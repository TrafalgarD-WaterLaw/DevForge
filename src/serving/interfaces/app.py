"""FastAPI application entry point."""
import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from serving.interfaces.memory_routes import router as memory_router
from serving.interfaces.project_routes import router as project_router
from serving.interfaces.run_routes import router as run_router

_log = logging.getLogger(__name__)

def auth_token() -> str:
    """配置的访问令牌（空 = 不启用鉴权）。"""
    from core.config import load_pipeline_config
    return str(load_pipeline_config().get("auth_token", "") or "").strip()

def create_app() -> FastAPI:
    app = FastAPI(title="DevForge API", version="2.0.0")
    # allow_credentials=True + allow_origins=["*"] is rejected by browsers —
    # the API uses no cookies/auth, so credentials stay off .
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

    app.include_router(run_router)
    app.include_router(project_router)
    app.include_router(memory_router)

    # 访问令牌鉴权：auth_token 非空时所有 /api 请求必须带 X-Auth-Token。
    # 服务监听 0.0.0.0 —— 不设令牌则局域网内任何人可消耗你的 API key。
    @app.middleware("http")
    async def _token_guard(request: Request, call_next):
        token = auth_token()
        if request.url.path.startswith("/api") \
                and request.method != "OPTIONS":
            if not token:
                pass   # 未配置 → 不启用（startup 会打警告）
            elif request.headers.get("x-auth-token") != token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    return app

def main():
    from core.logging import configure_logging
    configure_logging()   # 结构化日志：所有记录带 [run=xxxx]
    if not auth_token():
        _log.warning("auth_token 未配置 —— API 无鉴权。服务监听 0.0.0.0，"
                     "局域网内任何设备可访问并消耗你的 API key。"
                     "请在 configs/default.json 设置 auth_token。")
    app = create_app()
    uvicorn.run(
        "serving.interfaces.app:create_app",
        host="0.0.0.0", port=8000,
        reload=True,
        reload_dirs=["src", "configs"],
        factory=True,
        log_level="info",
    )

if __name__ == "__main__":
    main()
