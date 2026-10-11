"""Discord Webhook へ PDF を添付して送信する。"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests

from src.errors import PipelineError

logger = logging.getLogger(__name__)
STEP = "discord"

# Webhook の添付ファイル上限 (ブーストなしサーバーは 10MB)
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ATTEMPTS = 4


def build_message(topic: str, pdf_name: str, summary: str | None = None, when: datetime | None = None) -> str:
    when = when or datetime.now().astimezone()
    lines = [
        "**調査完了**",
        "",
        f"**テーマ：** {topic}",
        f"**調査日時：** {when.strftime('%Y-%m-%d %H:%M')}",
        f"**レポート：** {pdf_name}",
    ]
    if summary:
        lines += ["", "**要約：**", summary]
    # Discord の content 上限は 2000 文字
    text = "\n".join(lines)
    return text if len(text) <= 1900 else text[:1897] + "..."


def send_pdf(webhook_url: str, pdf_path: Path, message: str, *, timeout_sec: int = 60) -> None:
    if not webhook_url:
        raise PipelineError(STEP, "DISCORD_WEBHOOK_URL が設定されていません")
    if not webhook_url.startswith("https://"):
        raise PipelineError(STEP, "DISCORD_WEBHOOK_URL の形式が不正です (https:// で始まる URL が必要です)")
    if not pdf_path.is_file():
        raise PipelineError(STEP, f"送信する PDF が存在しません: {pdf_path}")
    size = pdf_path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise PipelineError(STEP, f"PDF が大きすぎます ({size / 1024 / 1024:.1f}MB > 10MB)")

    payload = {
        "content": message,
        "attachments": [{"id": 0, "filename": pdf_path.name}],
        "allowed_mentions": {"parse": []},
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with pdf_path.open("rb") as f:
                resp = requests.post(
                    webhook_url,
                    params={"wait": "true"},
                    data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                    files={"files[0]": (pdf_path.name, f, "application/pdf")},
                    timeout=timeout_sec,
                )
        except requests.Timeout as e:
            err = PipelineError(STEP, f"Discord への送信がタイムアウトしました ({timeout_sec}秒)")
            err.__cause__ = e
            wait = 2 ** attempt
        except requests.RequestException as e:
            err = PipelineError(STEP, f"Discord への送信に失敗しました: {e}")
            err.__cause__ = e
            wait = 2 ** attempt
        else:
            if resp.ok:
                logger.info("Discord へ送信しました: %s", pdf_path.name)
                return
            if resp.status_code == 429:
                err = PipelineError(STEP, "Discord のレート制限に達しました (HTTP 429)")
                wait = _retry_after(resp)
            elif resp.status_code in (401, 403, 404):
                raise PipelineError(
                    STEP, f"Discord Webhook が無効です (HTTP {resp.status_code}): {resp.text[:200]}",
                    hint="DISCORD_WEBHOOK_URL が正しいか、Webhook が削除されていないか確認してください",
                )
            elif resp.status_code < 500:
                raise PipelineError(STEP, f"Discord がリクエストを拒否しました (HTTP {resp.status_code}): {resp.text[:300]}")
            else:
                err = PipelineError(STEP, f"Discord サーバーエラー (HTTP {resp.status_code})")
                wait = 2 ** attempt

        if attempt < MAX_ATTEMPTS:
            logger.warning("%s — %.1f 秒後に再試行します (%d/%d)", err.message, wait, attempt, MAX_ATTEMPTS)
            time.sleep(wait)
    raise err


def _retry_after(resp) -> float:
    try:
        return float(resp.json().get("retry_after", 5))
    except (ValueError, AttributeError):
        return float(resp.headers.get("Retry-After", 5))
