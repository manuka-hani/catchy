"""調査依頼 (research_request) のスキーマ。

ResearchRequest がパイプラインの共通インターフェース。
CLI / HTTP / M5Stack のどこから来た依頼も、最終的にこの形にしてから調査に渡す。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["research", "comparison", "catch_up", "how_to", "other"]
TimeRange = Literal["recent", "past_week", "past_month", "past_year", "all_time", "custom"]
Depth = Literal["quick", "standard", "comprehensive"]


class RequestInfo(BaseModel):
    original_transcription: str
    corrected_transcription: str | None = Field(
        default=None, description="誤認識補正・フィラー除去後の発話"
    )
    normalized_query: str = Field(min_length=1)
    intent: Intent = "research"


class ResearchSpec(BaseModel):
    topic: str = Field(min_length=1)
    scope: str = ""
    time_range: TimeRange = "recent"
    time_range_note: str | None = Field(
        default=None, description="time_range が custom の場合などの補足 (例: 2026年以降)"
    )
    depth: Depth = "comprehensive"
    search_targets: list[str] = Field(default_factory=list)
    key_questions: list[str] = Field(default_factory=list, description="調査で答えるべき問い")
    comparison_targets: list[str] = Field(default_factory=list, description="比較対象 (比較依頼の場合)")
    preferred_sources: list[str] = Field(default_factory=list, description="ユーザーが指定した情報源")


class OutputSpec(BaseModel):
    format: Literal["pdf"] = "pdf"
    language: str = "ja"


class ResearchRequest(BaseModel):
    type: Literal["research_request"] = "research_request"
    version: str = "1.0"
    created_at: datetime
    request: RequestInfo
    research: ResearchSpec
    output: OutputSpec = Field(default_factory=OutputSpec)


class GeminiResearchDraft(BaseModel):
    """Gemini に生成させる部分だけを切り出したスキーマ。

    type / version / created_at / original_transcription はプログラム側で埋める
    (LLM に任せる必要がなく、改変されると困るため)。
    """

    is_research_request: bool = Field(
        description="発話が調査依頼として解釈できる場合 true。雑音や無関係な発話のみの場合 false"
    )
    corrected_transcription: str = Field(
        description="誤認識を文脈から補正し、フィラー(えー、あの等)を除去した発話全文"
    )
    normalized_query: str = Field(description="調査依頼を1文で簡潔にまとめたもの")
    intent: Intent = Field(description="依頼の種類")
    topic: str = Field(description="調査の主題 (短い名詞句)")
    scope: str = Field(description="調査範囲・観点 (例: 最新動向、料金比較、導入事例)")
    time_range: TimeRange = Field(description="調査対象の期間。指定がなく『最近』『最新』なら recent")
    time_range_note: str | None = Field(description="期間の補足。不要なら null")
    depth: Depth = Field(description="調査の深さ。特に指定がなければ comprehensive")
    search_targets: list[str] = Field(
        description="検索キーワード候補。日本語と英語の両方を含め 4〜10 個"
    )
    key_questions: list[str] = Field(description="この調査で答えるべき具体的な問い 3〜6 個")
    comparison_targets: list[str] = Field(description="比較対象。比較依頼でなければ空配列")
    preferred_sources: list[str] = Field(
        description="ユーザーが明示した情報源 (X, Reddit, Qiita 等)。指定がなければ空配列"
    )
    output_language: str = Field(description="レポートの言語。指定がなければ ja")
