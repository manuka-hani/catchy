"""STT → Gemini → Claude → PDF → Discord をつなぐパイプライン。

入力インターフェース (CLI / HTTP / M5Stack) はここの run_from_* を呼ぶだけでよい。
各ステップの結果は output/<run_name>/ に保存し、再実行時は既存の結果を再利用する。
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings
from src.errors import PipelineError
from src.notify import build_message, send_pdf
from src.pdf import generate_pdf
from src.request_builder import build_research_request
from src.research import run_research
from src.schemas import ResearchReport, ResearchRequest, SpeechTranscription, load_model, save_model
from src.stt import from_text, transcribe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def json_dir(self) -> Path:
        return self.root / "json"

    @property
    def transcription(self) -> Path:
        return self.json_dir / "transcription.json"

    @property
    def research_request(self) -> Path:
        return self.json_dir / "research_request.json"

    @property
    def research_report(self) -> Path:
        return self.json_dir / "research_report.json"

    @property
    def research_trace(self) -> Path:
        return self.json_dir / "research_trace.jsonl"

    @property
    def pdf(self) -> Path:
        return self.root / "pdf" / "research_report.pdf"

    @property
    def work_dir(self) -> Path:
        # Claude Agent SDK の作業ディレクトリ (ツールはWeb系のみだが念のため隔離する)
        return self.root / "work"


@dataclass
class PipelineResult:
    paths: RunPaths
    request: ResearchRequest
    report: ResearchReport
    pdf_path: Path
    sent_to_discord: bool


class Pipeline:
    def __init__(self, settings: Settings, run_name: str, *, resume: bool = True, send_discord: bool = True):
        self.settings = settings
        self.paths = RunPaths(settings.output_dir / run_name)
        self.resume = resume
        self.send_discord = send_discord

    # ---------- 入口 ----------
    def run_from_audio(self, wav_path: str | Path, *, source_type: str = "cli") -> PipelineResult:
        self._preflight()
        transcription = self._cached(self.paths.transcription, SpeechTranscription, "stt")
        if transcription is None:
            self._step(1, "音声認識 (faster-whisper)")
            transcription = transcribe(
                wav_path,
                source_type=source_type,
                model_name=self.settings.whisper_model,
                device=self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
            )
            save_model(transcription, self.paths.transcription)
            logger.info("保存: %s", self.paths.transcription)
        return self.run_from_transcription(transcription, _checked=True)

    def run_from_text(self, text: str, *, source_type: str = "text", filename: str | None = None) -> PipelineResult:
        self._preflight()
        transcription = self._cached(self.paths.transcription, SpeechTranscription, "stt")
        if transcription is None:
            self._step(1, "テキスト入力 (音声認識をスキップ)")
            transcription = from_text(text, source_type=source_type, filename=filename)
            save_model(transcription, self.paths.transcription)
        return self.run_from_transcription(transcription, _checked=True)

    def run_from_transcription(self, transcription: SpeechTranscription, *, _checked: bool = False) -> PipelineResult:
        if not _checked:
            self._preflight()
            save_model(transcription, self.paths.transcription)
        logger.info("文字起こし: %s", transcription.transcription.text)

        request = self._cached(self.paths.research_request, ResearchRequest, "gemini")
        if request is None:
            self._step(2, "調査依頼の構造化 (Gemini)")
            self.settings.require_gemini()
            request = build_research_request(
                transcription,
                api_key=self.settings.gemini_api_key,
                model=self.settings.gemini_model,
                timeout_sec=self.settings.gemini_timeout_sec,
            )
            save_model(request, self.paths.research_request)
            logger.info("保存: %s", self.paths.research_request)
        return self.run_from_request(request, _checked=True)

    def run_from_request(self, request: ResearchRequest, *, _checked: bool = False) -> PipelineResult:
        if not _checked:
            self._preflight(need_gemini=False)
            save_model(request, self.paths.research_request)

        report = self._cached(self.paths.research_report, ResearchReport, "research")
        if report is None:
            self._step(3, "Web 調査 (Claude Agent SDK)")
            report = run_research(
                request,
                work_dir=self.paths.work_dir,
                trace_path=self.paths.research_trace,
                model=self.settings.claude_model,
                max_turns=self.settings.claude_max_turns,
                max_budget_usd=self.settings.claude_max_budget_usd,
                timeout_sec=self.settings.claude_timeout_sec,
            )
            save_model(report, self.paths.research_report)
            logger.info("保存: %s", self.paths.research_report)

        # PDF は安価なので常に再生成する (レイアウト修正を反映しやすくするため)
        self._step(4, "PDF 生成")
        pdf_path = generate_pdf(
            report, self.paths.pdf, request,
            font_path=self.settings.pdf_font_path, bold_font_path=self.settings.pdf_bold_font_path,
        )

        sent = False
        if self.send_discord:
            self._step(5, "Discord 送信")
            send_pdf(
                self.settings.discord_webhook_url,
                pdf_path,
                build_message(report.topic, pdf_path.name, report.executive_summary),
            )
            sent = True
        else:
            logger.info("[5/5] Discord 送信: --no-discord が指定されたためスキップ")

        return PipelineResult(self.paths, request, report, pdf_path, sent)

    # ---------- 内部 ----------
    def _preflight(self, *, need_gemini: bool = True) -> None:
        """時間のかかる処理の前に、必要な設定が揃っているか確認する。"""
        has_request = self.resume and self.paths.research_request.exists()
        if need_gemini and not has_request:
            self.settings.require_gemini()
        if self.send_discord:
            self.settings.require_discord()
        if not self.settings.claude_code_oauth_token:
            logger.info("CLAUDE_CODE_OAUTH_TOKEN 未設定: Claude Code CLI のログイン情報で認証します")

    def _cached(self, path: Path, model, step: str):
        if not (self.resume and path.exists()):
            return None
        logger.info("既存の結果を再利用します: %s (再実行するには --fresh)", path)
        try:
            return load_model(model, path, step=step)
        except PipelineError as e:
            logger.warning("既存の結果が不正なため再生成します: %s", e.message)
            return None

    @staticmethod
    def _step(n: int, name: str) -> None:
        logger.info("[%d/5] %s", n, name)
