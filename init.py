"""Interactive first-run configuration for Ingot."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent / ".env"
PLACEHOLDERS = {"", "***", "replace-with-your-siliconflow-key", "your-api-key"}


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


def ask_secret(current: str) -> str:
    has_current = current not in PLACEHOLDERS
    hint = "（回车保留现有值）" if has_current else "（必填，输入不会显示）"
    while True:
        value = getpass.getpass(f"SiliconFlow API Key {hint}: ").strip()
        if value:
            return value
        if has_current:
            return current
        print("  API Key 不能为空。你也可以按 Ctrl+C 退出，稍后再配置。")


def needs_configuration(path: Path = ENV_PATH) -> bool:
    return read_env(path).get("EMBEDDING_API_KEY", "") in PLACEHOLDERS


def configure(path: Path = ENV_PATH) -> Path:
    current = read_env(path)
    print("\nIngot / 首次运行配置")
    print("=" * 38)
    print("配置只会写入本机 .env；该文件已被 .gitignore 排除。\n")

    embedding_base_url = ask(
        "Embedding API 地址", current.get("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
    )
    embedding_api_key = ask_secret(current.get("EMBEDDING_API_KEY", ""))
    embedding_model = ask("Embedding 模型", current.get("EMBEDDING_MODEL", "BAAI/bge-m3"))

    print("\n对话与 GraphRAG 可直接复用上面的地址和 Key。")
    chat_base_url = ask("Chat API 地址（留空表示复用）", current.get("CHAT_BASE_URL", ""))
    existing_chat_key = current.get("CHAT_API_KEY", "")
    if existing_chat_key:
        keep = input("Chat API Key 已单独配置，回车保留；输入 r 改为复用 Embedding Key: ").strip().lower()
        chat_api_key = "" if keep == "r" else existing_chat_key
    else:
        chat_api_key = ""
    chat_model = ask("Chat / 图谱抽取模型", current.get("CHAT_MODEL", "Qwen/Qwen3-8B"))

    print("\nOCR 会自动处理图片和扫描 PDF 页面，默认复用 SiliconFlow 地址和 Key。")
    ocr_enabled = ask_bool("启用 OCR", current.get("OCR_ENABLED", "true").lower() == "true")
    ocr_model = ask(
        "OCR 模型",
        current.get("OCR_MODEL", "PaddlePaddle/PaddleOCR-VL-1.5"),
    )
    ocr_max_pages = ask_int("单文档最大 OCR 页数", int(current.get("OCR_MAX_PAGES", "100")), 1, 2000)

    print("\nReranker 会对向量候选做二阶段精排，调用失败时自动回退。")
    rerank_enabled = ask_bool(
        "启用 Reranker", current.get("RERANK_ENABLED", "true").lower() == "true"
    )
    rerank_model = ask(
        "Reranker 模型",
        current.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
    )

    print("\n文本切分参数（中文资料的推荐默认值已经填好）。")
    chunk_size = ask_int("单块字符数", int(current.get("CHUNK_SIZE", "900")), 200, 8000)
    while True:
        chunk_overlap = ask_int("重叠字符数", int(current.get("CHUNK_OVERLAP", "160")), 0, 2000)
        if chunk_overlap < chunk_size:
            break
        print("  重叠字符数必须小于单块字符数。")

    content = f"""# 由 init.py 生成。此文件包含密钥，禁止提交到 Git。
EMBEDDING_BASE_URL={embedding_base_url}
EMBEDDING_API_KEY={embedding_api_key}
EMBEDDING_MODEL={embedding_model}
EMBEDDING_BATCH_SIZE={current.get('EMBEDDING_BATCH_SIZE', '16')}
EMBEDDING_TIMEOUT={current.get('EMBEDDING_TIMEOUT', '90')}

# 留空时复用 Embedding 服务地址和 Key。
CHAT_BASE_URL={chat_base_url}
CHAT_API_KEY={chat_api_key}
CHAT_MODEL={chat_model}
CHAT_TIMEOUT={current.get('CHAT_TIMEOUT', '180')}
CHAT_TEMPERATURE={current.get('CHAT_TEMPERATURE', '0.2')}
CHAT_MAX_TOKENS={current.get('CHAT_MAX_TOKENS', '2048')}

# OCR 与 Reranker 留空地址/Key 时复用 Embedding 服务。
OCR_ENABLED={str(ocr_enabled).lower()}
OCR_BASE_URL={current.get('OCR_BASE_URL', '')}
OCR_API_KEY={current.get('OCR_API_KEY', '')}
OCR_MODEL={ocr_model}
OCR_TIMEOUT={current.get('OCR_TIMEOUT', '240')}
OCR_CONCURRENCY={current.get('OCR_CONCURRENCY', '2')}
OCR_MIN_TEXT_CHARS={current.get('OCR_MIN_TEXT_CHARS', '80')}
OCR_MAX_PAGES={ocr_max_pages}
OCR_RENDER_DPI={current.get('OCR_RENDER_DPI', '144')}

RERANK_ENABLED={str(rerank_enabled).lower()}
RERANK_BASE_URL={current.get('RERANK_BASE_URL', '')}
RERANK_API_KEY={current.get('RERANK_API_KEY', '')}
RERANK_MODEL={rerank_model}
RERANK_CANDIDATES={current.get('RERANK_CANDIDATES', '18')}
RERANK_TIMEOUT={current.get('RERANK_TIMEOUT', '60')}

CHUNK_SIZE={chunk_size}
CHUNK_OVERLAP={chunk_overlap}
DEFAULT_TOP_K={current.get('DEFAULT_TOP_K', '6')}
GRAPH_CONCURRENCY={current.get('GRAPH_CONCURRENCY', '3')}
GRAPH_MAX_CHUNKS={current.get('GRAPH_MAX_CHUNKS', '0')}
GRAPH_CHUNK_TIMEOUT={current.get('GRAPH_CHUNK_TIMEOUT', '240')}
GRAPH_BUILD_TIMEOUT={current.get('GRAPH_BUILD_TIMEOUT', '3600')}
APP_HOST={current.get('APP_HOST', '127.0.0.1')}
APP_PORT={current.get('APP_PORT', '8000')}
DATA_DIR={current.get('DATA_DIR', './data')}
MAX_UPLOAD_MB={current.get('MAX_UPLOAD_MB', '50')}
"""
    path.write_text(content, encoding="utf-8")
    print(f"\n✓ 配置已保存到 {path}")
    print("  现在运行：python run.py\n")
    return path


if __name__ == "__main__":
    try:
        configure()
    except KeyboardInterrupt:
        print("\n已取消，未修改配置。")
