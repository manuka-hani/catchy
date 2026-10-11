"""Claude Agent SDK が出力する調査レポート (research_report) のスキーマ。

PDF 生成はこの構造化データだけを入力にするため、調査とレイアウトが疎結合になる。
"""

from typing import Literal

from pydantic import BaseModel, Field

Classification = Literal["FACT", "OPINION", "USER_REPORT", "INFERENCE"]
SourceType = Literal[
    "official_docs",
    "official_blog",
    "press_release",
    "github",
    "paper",
    "news",
    "tech_blog",
    "x",
    "reddit",
    "qiita",
    "zenn",
    "hacker_news",
    "stack_overflow",
    "other",
]
Reliability = Literal["high", "medium", "low"]


class Source(BaseModel):
    id: int = Field(description="本文中の参照番号 [n] と対応する連番 (1始まり)")
    title: str
    url: str
    source_type: SourceType
    publisher: str = Field(description="サイト名・組織名・投稿者など")
    published_date: str | None = Field(description="公開日 (YYYY-MM-DD 等)。不明なら null")
    accessed_date: str = Field(description="取得日 (YYYY-MM-DD)")
    reliability: Reliability


class ClassifiedStatement(BaseModel):
    statement: str = Field(description="本文。末尾に [n] 形式で出典番号を付ける")
    classification: Classification
    source_ids: list[int]


class ReportSection(BaseModel):
    heading: str
    paragraphs: list[str] = Field(description="段落。各段落に [n] 形式で出典番号を付ける")
    bullets: list[str] = Field(description="箇条書き。不要なら空配列")


class ResearchNotes(BaseModel):
    search_queries: list[str] = Field(description="実際に使用した検索クエリ")
    limitations: str = Field(description="調査の限界・取得できなかった情報など")


class ResearchReport(BaseModel):
    title: str
    topic: str
    overview: str = Field(description="調査概要: 何をどの範囲・方法で調べたか")
    executive_summary: str = Field(description="結論の要約 (3〜6文)")
    key_findings: list[str] = Field(description="主要な発見 (各項目に [n])")
    sections: list[ReportSection] = Field(description="詳細調査の章。テーマに応じて柔軟に構成する")
    important_facts: list[ClassifiedStatement]
    notable_opinions: list[ClassifiedStatement]
    analysis: str = Field(description="分析・考察。公式発表と実際のユーザー反応の比較を含める")
    conclusion: str
    sources: list[Source]
    research_notes: ResearchNotes
