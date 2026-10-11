import json
import logging
from dataclasses import replace

import pytest

import src.main as cli
from src import pipeline as pl
from src.errors import PipelineError
from src.schemas import ResearchReport, ResearchRequest
from src.stt import from_text


@pytest.fixture
def mocks(monkeypatch, report_data, request_data):
    """外部 API を呼ぶステップを差し替え、呼び出し回数を記録する。"""
    calls = {"stt": 0, "gemini": 0, "research": 0, "discord": 0}

    def fake_transcribe(path, **kw):
        calls["stt"] += 1
        return from_text("AIエージェントについて調べて", source_type=kw.get("source_type", "cli"))

    def fake_gemini(t, **kw):
        calls["gemini"] += 1
        return ResearchRequest.model_validate(request_data)

    def fake_research(req, **kw):
        calls["research"] += 1
        return ResearchReport.model_validate(report_data)

    def fake_send(url, pdf, msg):
        calls["discord"] += 1
        assert pdf.exists()

    monkeypatch.setattr(pl, "transcribe", fake_transcribe)
    monkeypatch.setattr(pl, "build_research_request", fake_gemini)
    monkeypatch.setattr(pl, "run_research", fake_research)
    monkeypatch.setattr(pl, "send_pdf", fake_send)
    return calls


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "sample.wav"
    p.write_bytes(b"RIFF" + b"\0" * 100)
    return p


def test_full_flow_writes_debug_outputs(settings, mocks, wav):
    result = pl.Pipeline(settings, "run1").run_from_audio(wav, source_type="m5stack")
    paths = result.paths
    assert paths.transcription.exists()
    assert paths.research_request.exists()
    assert paths.research_report.exists()
    assert result.pdf_path.exists()
    assert json.loads(paths.transcription.read_text(encoding="utf-8"))["source"]["type"] == "m5stack"
    assert mocks == {"stt": 1, "gemini": 1, "research": 1, "discord": 1}


def test_resume_skips_completed_steps(settings, mocks, wav):
    pl.Pipeline(settings, "run1").run_from_audio(wav)
    pl.Pipeline(settings, "run1").run_from_audio(wav)
    assert mocks["stt"] == 1 and mocks["gemini"] == 1 and mocks["research"] == 1
    assert mocks["discord"] == 2  # PDF 生成・送信は毎回行う


def test_fresh_reruns_everything(settings, mocks, wav):
    pl.Pipeline(settings, "run1").run_from_audio(wav)
    pl.Pipeline(settings, "run1", resume=False).run_from_audio(wav)
    assert mocks["stt"] == 2 and mocks["gemini"] == 2 and mocks["research"] == 2


def test_missing_gemini_key_fails_before_stt(settings, mocks, wav):
    s = replace(settings, gemini_api_key="")
    with pytest.raises(PipelineError, match="GEMINI_API_KEY") as e:
        pl.Pipeline(s, "run1").run_from_audio(wav)
    assert e.value.step == "config"
    assert mocks["stt"] == 0


def test_missing_discord_webhook(settings, mocks, wav):
    s = replace(settings, discord_webhook_url="")
    with pytest.raises(PipelineError, match="DISCORD_WEBHOOK_URL"):
        pl.Pipeline(s, "run1").run_from_audio(wav)
    # --no-discord なら最後まで動く
    result = pl.Pipeline(s, "run1", send_discord=False).run_from_audio(wav)
    assert not result.sent_to_discord and result.pdf_path.exists()


def test_request_input_does_not_need_gemini(settings, mocks, request_data):
    s = replace(settings, gemini_api_key="")
    req = ResearchRequest.model_validate(request_data)
    pl.Pipeline(s, "run1").run_from_request(req)
    assert mocks["gemini"] == 0 and mocks["research"] == 1


def test_corrupt_cached_json_is_regenerated(settings, mocks, wav):
    p = pl.Pipeline(settings, "run1")
    p.paths.research_request.parent.mkdir(parents=True)
    p.paths.research_request.write_text("{broken", encoding="utf-8")
    p.run_from_audio(wav)
    assert mocks["gemini"] == 1


# ---------- CLI ----------

def run_cli(monkeypatch, settings, argv):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    return cli.main(argv)


def test_cli_missing_wav(monkeypatch, settings, caplog, tmp_path):
    caplog.set_level(logging.ERROR)
    code = run_cli(monkeypatch, settings, [str(tmp_path / "nope.wav")])
    assert code == 1
    assert "入力ファイルが存在しません" in caplog.text


def test_cli_no_api_key(monkeypatch, settings, mocks, wav, caplog):
    caplog.set_level(logging.ERROR)
    code = run_cli(monkeypatch, replace(settings, gemini_api_key=""), [str(wav)])
    assert code == 1
    assert "'config'" in caplog.text and "GEMINI_API_KEY" in caplog.text


def test_cli_text_input(monkeypatch, settings, mocks, fixtures):
    code = run_cli(monkeypatch, settings, [str(fixtures / "ambiguous.txt"), "--no-discord"])
    assert code == 0
    assert mocks["stt"] == 0 and mocks["gemini"] == 1
    t = json.loads((settings.output_dir / "ambiguous" / "json" / "transcription.json").read_text(encoding="utf-8"))
    assert "AIエージェント" in t["transcription"]["text"]


def test_cli_unknown_json(monkeypatch, settings, tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    p = tmp_path / "x.json"
    p.write_text('{"type": "something"}', encoding="utf-8")
    assert run_cli(monkeypatch, settings, [str(p)]) == 1
    assert "未対応の JSON" in caplog.text
