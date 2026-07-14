import socket
from pathlib import Path

import pytest

from app.config import Settings
from init import needs_configuration, read_env, write_env_updates
from run import ensure_port_available


def test_env_parser_and_configuration_check(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# comment\nEMBEDDING_API_KEY=abc123\nCHAT_MODEL='model-name'\n", encoding="utf-8")
    assert read_env(path)["CHAT_MODEL"] == "model-name"
    assert needs_configuration(path) is False

    path.write_text("EMBEDDING_API_KEY=***\n", encoding="utf-8")
    assert needs_configuration(path) is True


def test_legacy_database_is_migrated_to_ingot_name(tmp_path: Path):
    legacy_database = tmp_path / "knowledge_forge.db"
    legacy_database.write_bytes(b"existing database")
    settings = Settings(_env_file=None, data_dir=tmp_path)

    settings.ensure_directories()

    assert settings.database_path == tmp_path / "ingot.db"
    assert settings.database_path.read_bytes() == b"existing database"
    assert not legacy_database.exists()


def test_run_reports_an_occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        with pytest.raises(SystemExit, match="已被占用"):
            ensure_port_available("127.0.0.1", port)


def test_env_updates_are_atomic_and_preserve_unrelated_values(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# keep this comment\nUNCHANGED=yes\nEMBEDDING_MODEL=old\n", encoding="utf-8")

    write_env_updates(
        {"EMBEDDING_MODEL": "model with spaces", "EMBEDDING_API_KEY": "secret#value"},
        path,
    )

    values = read_env(path)
    assert values["UNCHANGED"] == "yes"
    assert values["EMBEDDING_MODEL"] == "model with spaces"
    assert values["EMBEDDING_API_KEY"] == "secret#value"
    assert "# keep this comment" in path.read_text(encoding="utf-8")
    assert not path.with_name(".env.tmp").exists()

