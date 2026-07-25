"""Interactive server configuration for Ingot.

Model API URLs and keys are configured per browser device and never written here.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent / ".env"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _encode_env_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("配置值不能包含换行符")
    if "${" in value:
        raise ValueError("配置值不能包含 ${...}，以免被 .env 变量插值改写")
    if not value or all(character not in value for character in " \t#'\""):
        return value
    return json.dumps(value, ensure_ascii=False)


def write_env_updates(updates: dict[str, str], path: Path = ENV_PATH) -> Path:
    """Atomically update selected .env keys while preserving unrelated values and comments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    output: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key not in updates:
            output.append(raw_line)
            continue
        if key in pending:
            output.append(f"{key}={_encode_env_value(pending.pop(key))}")

    if pending:
        if output and output[-1]:
            output.append("")
        output.extend(f"{key}={_encode_env_value(value)}" for key, value in pending.items())

    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        temporary_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        os.replace(temporary_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def ask_int(label: str, default: int, minimum: int, maximum: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"  请输入 {minimum} 到 {maximum} 之间的整数。")


def ask_bool(label: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "1", "true", "是"}:
            return True
        if raw in {"n", "no", "0", "false", "否"}:
            return False
        print("  请输入 y 或 n。")


def needs_configuration(path: Path = ENV_PATH) -> bool:
    return not read_env(path).get("DEVICE_COOKIE_SECRET", "").strip()


def configure(path: Path = ENV_PATH) -> Path:
    current = read_env(path)
    print("\nIngot / 服务端配置")
    print("=" * 38)
    print("这里只配置服务器参数；模型 API 地址和 Key 请在每台设备的浏览器中设置。\n")

    app_host = ask("监听地址", current.get("APP_HOST", "127.0.0.1"))
    app_port = ask_int("监听端口", int(current.get("APP_PORT", "8000")), 1, 65535)
    data_dir = ask("数据目录", current.get("DATA_DIR", "./data"))
    max_upload_mb = ask_int(
        "单文件大小上限（MB）",
        int(current.get("MAX_UPLOAD_MB", "50")),
        1,
        1024,
    )
    cookie_secret = current.get("DEVICE_COOKIE_SECRET", "").strip() or secrets.token_urlsafe(48)

    write_env_updates(
        {
            "APP_HOST": app_host,
            "APP_PORT": str(app_port),
            "DATA_DIR": data_dir,
            "MAX_UPLOAD_MB": str(max_upload_mb),
            "DEVICE_COOKIE_SECRET": cookie_secret,
        },
        path,
    )
    print(f"\n✓ 配置已保存到 {path}")
    print("  设备 Cookie 加密密钥已安全生成/保留，不会在终端显示。")
    print("  现在运行：python run.py\n")
    return path


if __name__ == "__main__":
    try:
        configure()
    except KeyboardInterrupt:
        print("\n已取消，未修改配置。")
