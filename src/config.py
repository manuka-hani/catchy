"""環境変数 / .env から設定を読み込む。

秘密情報はソースコードに書かず、必ずここ経由で取得する。
"""

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"環境変数 {name} は整数である必要があります (現在値: {raw!r})") from e


def _float_env(name: str, default: float | None) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(f"環境変数 {name} は数値である必要があります (現在値: {raw!r})") from e


@dataclass(frozen=True)
class Settings:
    # Gemini
    gemini_api_key: str
    gemini_model: str
    gemini_timeout_sec: int

    # Claude Agent SDK
    anthropic_api_key: str
    claude_model: str | None
    claude_max_turns: int
    claude_max_budget_usd: float | None
    claude_timeout_sec: int

    # Discord
    discord_webhook_url: str

    # faster-whisper
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str

    # PDF
    pdf_font_path: str
    pdf_bold_font_path: str

    output_dir: Path

    def require_gemini(self) -> None:
        if not self.gemini_api_key:
            raise ConfigError(
                "GEMINI_API_KEY が設定されていません",
                hint=".env に GEMINI_API_KEY=... を設定してください (https://aistudio.google.com/apikey)",
            )

    def require_discord(self) -> None:
        if not self.discord_webhook_url:
            raise ConfigError(
                "DISCORD_WEBHOOK_URL が設定されていません",
                hint=".env に DISCORD_WEBHOOK_URL=... を設定するか、--no-discord を付けて実行してください",
            )


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    """.env を読み込み Settings を返す。既に設定済みの環境変数は上書きしない。"""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    output_dir = Path(os.getenv("OUTPUT_DIR", "").strip() or PROJECT_ROOT / "output")

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.8-flash",
        gemini_timeout_sec=_int_env("GEMINI_TIMEOUT_SEC", 120),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        claude_model=os.getenv("CLAUDE_MODEL", "").strip() or None,
        claude_max_turns=_int_env("CLAUDE_MAX_TURNS", 80),
        claude_max_budget_usd=_float_env("CLAUDE_MAX_BUDGET_USD", None),
        claude_timeout_sec=_int_env("CLAUDE_TIMEOUT_SEC", 2400),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        whisper_model=os.getenv("WHISPER_MODEL", "").strip() or "small",
        whisper_device=os.getenv("WHISPER_DEVICE", "").strip() or "cpu",
        whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "").strip() or "int8",
        pdf_font_path=os.getenv("PDF_FONT_PATH", "").strip(),
        pdf_bold_font_path=os.getenv("PDF_BOLD_FONT_PATH", "").strip(),
        output_dir=output_dir,
    )


def setup_logging(verbose: bool = False) -> None:
    # Windows のコンソール (cp932) でも日本語・記号のログで落ちないようにする
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 外部ライブラリの冗長なログは抑える
    for noisy in ("httpx", "httpx2", "httpcore", "urllib3", "huggingface_hub", "google_genai", "faster_whisper", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
