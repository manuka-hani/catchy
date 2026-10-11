import json
from types import SimpleNamespace

import pytest

from src.errors import PipelineError
from src.request_builder.gemini import build_research_request
from src.stt import from_text


class FakeModels:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text, candidates=[])


def fake_client(**kw):
    return SimpleNamespace(models=FakeModels(**kw))


def draft(**overrides):
    d = {
        "is_research_request": True,
        "corrected_transcription": "Claude Code と Codex と Gemini CLI の最新動向を比較して",
        "normalized_query": "Claude Code・Codex・Gemini CLI の最新動向を比較する",
        "intent": "comparison",
        "topic": "AIコーディングCLIツール比較",
        "scope": "最新動向の比較",
        "time_range": "recent",
        "time_range_note": None,
        "depth": "comprehensive",
        "search_targets": ["Claude Code", "OpenAI Codex CLI", "Gemini CLI"],
        "key_questions": ["各ツールの最新アップデートは?"],
        "comparison_targets": ["Claude Code", "Codex", "Gemini CLI"],
        "preferred_sources": [],
        "output_language": "ja",
    }
    d.update(overrides)
    return d


def test_specific_request_is_structured():
    t = from_text("クロードコードとコーデックスとジェミニCLIの最新動向を比較して")
    client = fake_client(text=json.dumps(draft(), ensure_ascii=False))
    req = build_research_request(t, api_key="k", model="m", client=client)

    assert req.type == "research_request"
    assert req.request.original_transcription == t.transcription.text  # 原文はプログラム側で保持
    assert req.request.intent == "comparison"
    assert req.research.comparison_targets == ["Claude Code", "Codex", "Gemini CLI"]
    call = client.models.calls[0]
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema["type"] == "object"


def test_invalid_json_from_gemini():
    t = from_text("AIエージェントについて調べて")
    with pytest.raises(PipelineError, match="JSON として解析できません") as e:
        build_research_request(t, api_key="k", model="m", client=fake_client(text="not json"))
    assert e.value.step == "gemini"


def test_schema_violation_from_gemini():
    t = from_text("AIエージェントについて調べて")
    bad = draft(depth="ultra")
    with pytest.raises(PipelineError, match="depth"):
        build_research_request(t, api_key="k", model="m", client=fake_client(text=json.dumps(bad)))


def test_not_a_research_request():
    t = from_text("あー、えー")
    client = fake_client(text=json.dumps(draft(is_research_request=False)))
    with pytest.raises(PipelineError, match="調査依頼として解釈できません"):
        build_research_request(t, api_key="k", model="m", client=client)


@pytest.mark.parametrize("code,expected", [(429, "レート制限"), (403, "認証"), (404, "見つかりません")])
def test_api_errors_are_explained(code, expected):
    from google.genai import errors

    exc = errors.ClientError(code, {"error": {"code": code, "message": "boom", "status": "X"}})
    t = from_text("AIエージェントについて調べて")
    with pytest.raises(PipelineError, match=expected):
        build_research_request(t, api_key="k", model="m", client=fake_client(exc=exc))


def test_timeout_is_wrapped():
    t = from_text("AIエージェントについて調べて")
    with pytest.raises(PipelineError, match="TimeoutError"):
        build_research_request(t, api_key="k", model="m", client=fake_client(exc=TimeoutError("slow")))
