import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from src.errors import PipelineError

from .report import ResearchReport
from .research_request import GeminiResearchDraft, ResearchRequest
from .transcription import SpeechTranscription

__all__ = [
    "GeminiResearchDraft",
    "ResearchReport",
    "ResearchRequest",
    "SpeechTranscription",
    "inline_json_schema",
    "load_model",
    "save_model",
    "validate_model",
]

M = TypeVar("M", bound=BaseModel)


def format_validation_error(e: ValidationError) -> str:
    lines = []
    for err in e.errors()[:10]:
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def validate_model(model: type[M], data: dict | str, *, step: str, label: str) -> M:
    """dict / JSON 文字列を検証し、失敗時は分かりやすい PipelineError にする。"""
    try:
        if isinstance(data, str):
            return model.model_validate_json(data)
        return model.model_validate(data)
    except ValidationError as e:
        raise PipelineError(
            step, f"{label} の形式が不正です:\n{format_validation_error(e)}"
        ) from e


def load_model(model: type[M], path: Path, *, step: str) -> M:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PipelineError(step, f"{path} を読み込めません: {e}") from e
    return validate_model(model, text, step=step, label=str(path))


def save_model(obj: BaseModel, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def inline_json_schema(model: type[BaseModel]) -> dict:
    """$defs / $ref を展開した JSON Schema を返す (LLM の構造化出力向け)。"""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return resolve(dict(defs[name]))
            # 注釈としての "title": "<str>" だけを落とす ("title" という名前のプロパティは残す)
            return {
                k: resolve(v)
                for k, v in node.items()
                if not (k == "title" and isinstance(v, str))
            }
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
