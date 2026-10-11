"""ResearchReport から日本語 PDF を生成する。

入力は構造化データ (ResearchReport) のみ。調査ロジックには依存しない。
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from src.errors import PipelineError
from src.schemas import ResearchReport, ResearchRequest

from .fonts import find_japanese_font

logger = logging.getLogger(__name__)
STEP = "pdf"

FONT = "jp"
ACCENT = (37, 82, 160)
MUTED = (110, 110, 110)
TEXT = (30, 30, 30)

CLASSIFICATION_LABELS = {
    "FACT": "事実",
    "OPINION": "意見",
    "USER_REPORT": "ユーザー報告",
    "INFERENCE": "推論",
}
SOURCE_TYPE_LABELS = {
    "official_docs": "公式ドキュメント",
    "official_blog": "公式ブログ",
    "press_release": "プレスリリース",
    "github": "GitHub",
    "paper": "論文",
    "news": "ニュース",
    "tech_blog": "技術ブログ",
    "x": "X",
    "reddit": "Reddit",
    "qiita": "Qiita",
    "zenn": "Zenn",
    "hacker_news": "Hacker News",
    "stack_overflow": "Stack Overflow",
    "other": "その他",
}
RELIABILITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _clean(text: str) -> str:
    """Markdown の強調記号などを除去する (PDF ではプレーンテキストとして描画するため)。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = text.replace("`", "")
    return text.strip()


def _build_pdf_class():
    from fpdf import FPDF

    class ReportPDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font(FONT, size=8)
            self.set_text_color(*MUTED)
            self.cell(0, 6, f"{self.page_no()} / {{nb}}", align="C")

    return ReportPDF


class _Writer:
    def __init__(self, pdf):
        self.pdf = pdf

    @property
    def width(self) -> float:
        return self.pdf.w - self.pdf.l_margin - self.pdf.r_margin

    def text(self, text: str, *, size=10.5, bold=False, color=TEXT, line_h=6.2, indent=0.0, link=None):
        from fpdf.enums import WrapMode, XPos, YPos

        self.pdf.set_font(FONT, style="B" if bold else "", size=size)
        self.pdf.set_text_color(*color)
        self.pdf.set_x(self.pdf.l_margin + indent)
        self.pdf.multi_cell(
            self.width - indent, line_h, _clean(text), align="L", wrapmode=WrapMode.CHAR,
            new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=link,
        )

    def gap(self, h=3.0):
        self.pdf.ln(h)

    def heading(self, text: str, level: int = 1):
        if self.pdf.get_y() > self.pdf.h - 45:
            self.pdf.add_page()
        if level == 1:
            self.gap(5)
            self.text(text, size=15, bold=True, color=ACCENT, line_h=8)
            y = self.pdf.get_y()
            self.pdf.set_draw_color(*ACCENT)
            self.pdf.set_line_width(0.5)
            self.pdf.line(self.pdf.l_margin, y, self.pdf.w - self.pdf.r_margin, y)
            self.gap(3)
        else:
            self.gap(3)
            self.text(text, size=12.5, bold=True, color=TEXT, line_h=7)
            self.gap(1)

    def paragraph(self, text: str):
        if text and text.strip():
            self.text(text)
            self.gap(2)

    def bullets(self, items: list[str], marker: str = "・"):
        for item in items:
            if not item.strip():
                continue
            y = self.pdf.get_y()
            self.pdf.set_font(FONT, size=10.5)
            self.pdf.set_text_color(*ACCENT)
            self.pdf.set_xy(self.pdf.l_margin + 1, y)
            self.pdf.cell(5, 6.2, marker)
            self.pdf.set_xy(self.pdf.l_margin + 6, y)
            self.text(item, indent=6)
            self.gap(1)
        self.gap(1)

    def tagged(self, label: str, text: str):
        """[事実] のようなラベル付きの項目。"""
        self.text(f"[{label}]", size=8.5, bold=True, color=ACCENT, line_h=5, indent=2)
        self.text(text, indent=2)
        self.gap(2)


def generate_pdf(report: ResearchReport, output_path: Path, request: ResearchRequest | None = None,
                 *, font_path: str = "", bold_font_path: str = "") -> Path:
    try:
        fonts = find_japanese_font(font_path, bold_font_path)
        logger.info("PDF を生成しています (font=%s)...", fonts.regular.name)

        pdf = _build_pdf_class()(format="A4")
        pdf.set_margins(18, 18, 18)
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_font(FONT, "", str(fonts.regular))
        pdf.add_font(FONT, "B", str(fonts.bold))
        pdf.set_title(report.title)
        pdf.set_creator("catchy research gadget")
        pdf.add_page()
        w = _Writer(pdf)

        # --- 表紙ヘッダ ---
        w.text("Research Report", size=11, bold=True, color=MUTED, line_h=6)
        w.gap(1)
        w.text(report.title, size=20, bold=True, color=ACCENT, line_h=10)
        w.gap(3)
        meta = [
            ("調査テーマ", report.topic),
            ("調査日時", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")),
            ("出典数", f"{len(report.sources)} 件"),
        ]
        if request:
            meta.insert(1, ("依頼内容", request.request.normalized_query))
        for label, value in meta:
            w.text(f"{label}：{value}", size=10, color=MUTED, line_h=5.5)
        w.gap(2)

        # --- 本文 ---
        w.heading("調査概要")
        w.paragraph(report.overview)

        w.heading("結論 / Executive Summary")
        w.paragraph(report.executive_summary)

        if report.key_findings:
            w.heading("主要な発見")
            w.bullets(report.key_findings)

        if report.sections:
            w.heading("詳細調査")
            for i, section in enumerate(report.sections, 1):
                # LLM が見出しに付けた番号 ("1." "第1章" 等) は除去して振り直す
                heading = re.sub(r"^\s*(第?\d+[章.．、)）]?)\s*", "", section.heading)
                w.heading(f"{i}. {heading}", level=2)
                for p in section.paragraphs:
                    w.paragraph(p)
                if section.bullets:
                    w.bullets(section.bullets)

        if report.important_facts:
            w.heading("重要な事実")
            for st in report.important_facts:
                w.tagged(CLASSIFICATION_LABELS.get(st.classification, st.classification), st.statement)

        if report.notable_opinions:
            w.heading("注目すべき意見")
            for st in report.notable_opinions:
                w.tagged(CLASSIFICATION_LABELS.get(st.classification, st.classification), st.statement)

        w.heading("分析・考察")
        for p in report.analysis.split("\n"):
            w.paragraph(p)

        w.heading("結論")
        for p in report.conclusion.split("\n"):
            w.paragraph(p)

        # --- 出典 ---
        w.heading("参考資料・出典")
        for src in sorted(report.sources, key=lambda s: s.id):
            w.text(f"[{src.id}] {src.title}", size=10, bold=True, line_h=5.5)
            info = " / ".join(
                x for x in [
                    src.publisher,
                    SOURCE_TYPE_LABELS.get(src.source_type, src.source_type),
                    f"公開日: {src.published_date or '不明'}",
                    f"取得日: {src.accessed_date}",
                    f"信頼性: {RELIABILITY_LABELS.get(src.reliability, src.reliability)}",
                ] if x
            )
            w.text(info, size=8.5, color=MUTED, line_h=4.8, indent=4)
            w.text(src.url, size=8.5, color=ACCENT, line_h=4.8, indent=4, link=src.url)
            w.gap(2)

        # --- 調査メモ ---
        notes = report.research_notes
        if notes.search_queries or notes.limitations:
            w.heading("調査メモ")
            if notes.search_queries:
                w.text("使用した検索クエリ", size=10, bold=True)
                w.text("、".join(notes.search_queries), size=9, color=MUTED, line_h=5)
                w.gap(2)
            if notes.limitations:
                w.text("調査の限界", size=10, bold=True)
                w.text(notes.limitations, size=9, color=MUTED, line_h=5)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(output_path))
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(STEP, f"PDF の生成に失敗しました ({type(e).__name__}: {e})") from e

    logger.info("PDF を保存しました: %s (%d ページ)", output_path, pdf.pages_count)
    return output_path
