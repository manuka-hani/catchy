import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.config import load_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # 実環境の .env / 環境変数に影響されないようにする
    for key in ("GEMINI_API_KEY", "DISCORD_WEBHOOK_URL", "CLAUDE_CODE_OAUTH_TOKEN", "OUTPUT_DIR"):
        monkeypatch.delenv(key, raising=False)
    s = load_settings(env_file=tmp_path / "nonexistent.env")
    return replace(
        s,
        gemini_api_key="test-gemini-key",
        discord_webhook_url="https://discord.com/api/webhooks/1/test",
        output_dir=tmp_path / "output",
    )


@pytest.fixture
def report_data(fixtures) -> dict:
    return json.loads((fixtures / "research_report.json").read_text(encoding="utf-8"))


@pytest.fixture
def request_data(fixtures) -> dict:
    return json.loads((fixtures / "research_request.json").read_text(encoding="utf-8"))
