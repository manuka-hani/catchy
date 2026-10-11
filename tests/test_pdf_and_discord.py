from types import SimpleNamespace

import pytest
import requests

from src.errors import PipelineError
from src.notify import discord
from src.pdf import generate_pdf
from src.schemas import ResearchReport, ResearchRequest


def test_generate_japanese_pdf(tmp_path, report_data, request_data):
    report = ResearchReport.model_validate(report_data)
    out = generate_pdf(report, tmp_path / "r.pdf", ResearchRequest.model_validate(request_data))
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    # 日本語フォントが埋め込まれていること
    assert b"/FontFile2" in data


def test_missing_font_path_is_reported(tmp_path, report_data):
    report = ResearchReport.model_validate(report_data)
    with pytest.raises(PipelineError, match="PDF_FONT_PATH") as e:
        generate_pdf(report, tmp_path / "r.pdf", font_path=str(tmp_path / "nope.ttf"))
    assert e.value.step == "pdf"


@pytest.fixture
def pdf_file(tmp_path):
    p = tmp_path / "research_report.pdf"
    p.write_bytes(b"%PDF-1.4 test")
    return p


def resp(status, body=None, headers=None):
    return SimpleNamespace(
        ok=200 <= status < 300, status_code=status, text=str(body),
        json=lambda: body or {}, headers=headers or {},
    )


def test_discord_success(monkeypatch, pdf_file):
    calls = []

    def fake_post(url, **kw):
        calls.append(kw)
        return resp(200)

    monkeypatch.setattr(discord.requests, "post", fake_post)
    discord.send_pdf("https://discord.com/api/webhooks/1/x", pdf_file, "msg")
    assert "files[0]" in calls[0]["files"]
    assert "payload_json" in calls[0]["data"]


def test_discord_missing_webhook(pdf_file):
    with pytest.raises(PipelineError, match="DISCORD_WEBHOOK_URL"):
        discord.send_pdf("", pdf_file, "msg")


def test_discord_invalid_webhook(monkeypatch, pdf_file):
    monkeypatch.setattr(discord.requests, "post", lambda url, **kw: resp(404, {"message": "Unknown Webhook"}))
    with pytest.raises(PipelineError, match="Webhook が無効"):
        discord.send_pdf("https://discord.com/api/webhooks/1/x", pdf_file, "msg")


def test_discord_rate_limit_then_success(monkeypatch, pdf_file):
    responses = iter([resp(429, {"retry_after": 0.01}), resp(200)])
    monkeypatch.setattr(discord.requests, "post", lambda url, **kw: next(responses))
    monkeypatch.setattr(discord.time, "sleep", lambda s: None)
    discord.send_pdf("https://discord.com/api/webhooks/1/x", pdf_file, "msg")


def test_discord_timeout_retries_then_fails(monkeypatch, pdf_file):
    def boom(url, **kw):
        raise requests.Timeout()

    monkeypatch.setattr(discord.requests, "post", boom)
    monkeypatch.setattr(discord.time, "sleep", lambda s: None)
    with pytest.raises(PipelineError, match="タイムアウト"):
        discord.send_pdf("https://discord.com/api/webhooks/1/x", pdf_file, "msg")


def test_message_is_truncated():
    msg = discord.build_message("テーマ", "a.pdf", "長い" * 2000)
    assert len(msg) <= 2000
    assert "調査完了" in msg and "テーマ" in msg
