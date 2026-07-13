from pathlib import Path

from init import needs_configuration, read_env


def test_env_parser_and_configuration_check(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# comment\nEMBEDDING_API_KEY=abc123\nCHAT_MODEL='model-name'\n", encoding="utf-8")
    assert read_env(path)["CHAT_MODEL"] == "model-name"
    assert needs_configuration(path) is False

    path.write_text("EMBEDDING_API_KEY=***\n", encoding="utf-8")
    assert needs_configuration(path) is True

