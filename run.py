"""Start Knowledge Forge and invoke the setup wizard when configuration is missing."""

from __future__ import annotations

import sys

import uvicorn

from init import configure, needs_configuration


if __name__ == "__main__":
    if needs_configuration():
        if not sys.stdin.isatty():
            raise SystemExit("EMBEDDING_API_KEY 未配置；请先在交互式终端运行 python init.py")
        configure()
    from app.config import get_settings

    settings = get_settings()
    print(f"Knowledge Forge: http://{settings.app_host}:{settings.app_port}")
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)
