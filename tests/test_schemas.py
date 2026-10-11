import pytest

from src.errors import PipelineError
from src.schemas import (
    GeminiResearchDraft,
    ResearchReport,
    ResearchRequest,
    SpeechTranscription,
    inline_json_schema,
    validate_model,
)


def test_research_request_fixture_is_valid(request_data):
    req = validate_model(ResearchRequest, request_data, step="t", label="req")
    assert req.research.topic == "AIエージェント"


def test_invalid_request_gives_readable_error(request_data):
    request_data["research"]["depth"] = "super-deep"
    del request_data["request"]["normalized_query"]
    with pytest.raises(PipelineError) as e:
        validate_model(ResearchRequest, request_data, step="gemini", label="Gemini の応答")
    assert e.value.step == "gemini"
    assert "research.depth" in e.value.message
    assert "request.normalized_query" in e.value.message


def test_broken_json_string():
    with pytest.raises(PipelineError):
        validate_model(ResearchRequest, "{not json", step="input", label="x")


def test_empty_transcription_rejected():
    with pytest.raises(PipelineError):
        validate_model(
            SpeechTranscription,
            {"created_at": "2026-10-11T22:46:30+09:00", "transcription": {"text": "  "}},
            step="stt", label="t",
        )


def test_spec_example_transcription_is_valid():
    data = {
        "type": "speech_transcription",
        "version": "1.0",
        "created_at": "2026-10-11T22:46:30+09:00",
        "language": "ja",
        "source": {"type": "m5stack", "audio_format": "wav"},
        "transcription": {
            "text": "AIエージェントについて最近の情報を網羅的に調査してください。",
            "segments": [{"start": 0.0, "end": 4.2, "text": "AIエージェントについて最近の情報を網羅的に調査してください。"}],
        },
    }
    t = validate_model(SpeechTranscription, data, step="t", label="t")
    assert t.source.type == "m5stack"


def test_inline_schema_keeps_title_property_and_has_no_refs():
    schema = inline_json_schema(ResearchReport)
    text = str(schema)
    assert "$ref" not in text and "$defs" not in text
    assert "title" in schema["properties"]
    assert "title" in schema["properties"]["sources"]["items"]["properties"]


def test_gemini_draft_schema_all_required():
    schema = inline_json_schema(GeminiResearchDraft)
    assert set(schema["required"]) == set(schema["properties"])
