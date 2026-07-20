"""Start Ingot and invoke the setup wizard when configuration is missing."""

from __future__ import annotations

import sys
import socket

import uvicorn

from init import configure, needs_configuration


def ensure_port_available(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise SystemExit(
                f"无法启动 Ingot：{host}:{port} 已被占用。请关闭旧服务后重试。"
            ) from exc


if __name__ == "__main__":
    if needs_configuration():
        if not sys.stdin.isatty():
            raise SystemExit("EMBEDDING_API_KEY 未配置；请先在交互式终端运行 python init.py")
        configure()
    from app.config import get_settings

    settings = get_settings()
    ensure_port_available(settings.app_host, settings.app_port)
    print(f"Ingot: http://{settings.app_host}:{settings.app_port}")
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)
