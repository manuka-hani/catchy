"""faster-whisper による日本語文字起こし。

結果は単純な TXT ではなく、セグメント情報を保った SpeechTranscription (JSON) として返す。
入力インターフェース (CLI / HTTP / M5Stack) には依存しない。
"""

import logging
import threading
from datetime import datetime
from pathlib import Path

from src.errors import PipelineError
from src.schemas.transcription import (
    SpeechTranscription,
    TranscriptionBody,
    TranscriptionModelInfo,
    TranscriptionSegment,
    TranscriptionSource,
)

logger = logging.getLogger(__name__)
STEP = "stt"

_models: dict[tuple[str, str, str], object] = {}
_lock = threading.Lock()


def get_model(name: str = "small", device: str = "cpu", compute_type: str = "int8"):
    """WhisperModel をプロセス内でキャッシュして返す (初回のみロード/ダウンロード)。"""
    key = (name, device, compute_type)
    with _lock:
        if key not in _models:
            try:
                from faster_whisper import WhisperModel

                logger.info("Whisper モデルをロードしています (%s, %s, %s)...", *key)
                _models[key] = WhisperModel(name, device=device, compute_type=compute_type)
            except Exception as e:
                raise PipelineError(
                    STEP,
                    f"Whisper モデル '{name}' のロードに失敗しました: {e}",
                    hint="初回はモデルのダウンロードが必要です。ネットワーク接続と WHISPER_MODEL の値を確認してください",
                ) from e
        return _models[key]


def _check_audio_file(path: Path) -> None:
    if not path.exists():
        raise PipelineError(STEP, f"音声ファイルが存在しません: {path}")
    if not path.is_file():
        raise PipelineError(STEP, f"音声ファイルではありません: {path}")
    # WAV ヘッダ (44 byte) 以下は中身がない
    if path.stat().st_size <= 44:
        raise PipelineError(STEP, f"音声ファイルが空、または壊れています: {path}")


def transcribe(
    wav_path: str | Path,
    *,
    source_type: str = "cli",
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "ja",
) -> SpeechTranscription:
    path = Path(wav_path)
    _check_audio_file(path)
    model = get_model(model_name, device, compute_type)

    logger.info("文字起こしを開始します: %s", path)
    try:
        segments_iter, info = model.transcribe(str(path), language=language, vad_filter=True)
        # segments はジェネレータなので、ここで消費した時点で実際のデコードが走る
        segments = [
            TranscriptionSegment(
                start=round(s.start, 2),
                end=round(s.end, 2),
                text=s.text.strip(),
                avg_logprob=round(s.avg_logprob, 4),
                no_speech_prob=round(s.no_speech_prob, 4),
                compression_ratio=round(s.compression_ratio, 4),
            )
            for s in segments_iter
        ]
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(
            STEP,
            f"音声のデコード/文字起こしに失敗しました ({type(e).__name__}: {e})",
            hint="WAV ファイルが壊れていないか確認してください",
        ) from e

    text = "".join(s.text for s in segments).strip()
    if not text:
        raise PipelineError(STEP, "音声から発話を検出できませんでした (文字起こし結果が空です)")

    logger.info(
        "文字起こし完了: %.1f秒, %dセグメント, %d文字", info.duration, len(segments), len(text)
    )
    return SpeechTranscription(
        created_at=datetime.now().astimezone(),
        language=info.language or language,
        language_probability=round(info.language_probability, 4),
        duration_sec=round(info.duration, 2),
        source=TranscriptionSource(
            type=source_type, audio_format=path.suffix.lstrip(".").lower() or "wav", filename=path.name
        ),
        model=TranscriptionModelInfo(name=model_name, device=device, compute_type=compute_type),
        transcription=TranscriptionBody(text=text, segments=segments),
    )


def from_text(text: str, *, source_type: str = "text", filename: str | None = None) -> SpeechTranscription:
    """テキスト入力 (音声なし) を SpeechTranscription に包む。STT をスキップしてテストする用途。"""
    text = text.strip()
    if not text:
        raise PipelineError(STEP, "入力テキストが空です")
    return SpeechTranscription(
        created_at=datetime.now().astimezone(),
        source=TranscriptionSource(type=source_type, audio_format=None, filename=filename),
        transcription=TranscriptionBody(text=text, segments=[]),
    )
