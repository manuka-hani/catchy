"""CLI エントリポイント。

    python -m src.main ./input_audio/sample.wav
    python -m src.main ./request.txt                  # 音声認識をスキップしてテキストから
    python -m src.main ./output/x/json/research_request.json   # Gemini もスキップ
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from src.config import load_settings, setup_logging
from src.errors import PipelineError
from src.pipeline import Pipeline
from src.schemas import ResearchRequest, SpeechTranscription, validate_model

logger = logging.getLogger("src.main")

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m src.main",
        description="音声/テキストの調査依頼から Web 調査レポート (PDF) を作り Discord に送る",
    )
    p.add_argument("input", help="入力ファイル (.wav 等の音声 / .txt / transcription・research_request の .json)")
    p.add_argument("--run-name", help="出力フォルダ名 (既定: 入力ファイル名)。同名なら途中結果を再利用する")
    p.add_argument("--source", default="cli", help="入力元の種別 (cli / m5stack / http)。transcription.json に記録される")
    p.add_argument("--fresh", action="store_true", help="既存の途中結果を使わず全ステップを再実行する")
    p.add_argument("--no-discord", action="store_true", help="Discord へ送信しない")
    p.add_argument("--output-dir", help="出力先ディレクトリ (既定: ./output または OUTPUT_DIR)")
    p.add_argument("-v", "--verbose", action="store_true", help="デバッグログを表示する")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.output_dir:
        from dataclasses import replace

        settings = replace(settings, output_dir=Path(args.output_dir))

    path = Path(args.input)
    if not path.exists():
        raise PipelineError("input", f"入力ファイルが存在しません: {path}")

    pipeline = Pipeline(
        settings,
        args.run_name or path.stem,
        resume=not args.fresh,
        send_discord=not args.no_discord,
    )
    logger.info("出力先: %s", pipeline.paths.root)

    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        result = pipeline.run_from_audio(path, source_type=args.source)
    elif suffix == ".txt":
        result = pipeline.run_from_text(path.read_text(encoding="utf-8"), filename=path.name)
    elif suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PipelineError("input", f"JSON として読み込めません: {path} ({e})") from e
        kind = data.get("type") if isinstance(data, dict) else None
        if kind == "speech_transcription":
            result = pipeline.run_from_transcription(
                validate_model(SpeechTranscription, data, step="input", label=str(path))
            )
        elif kind == "research_request":
            result = pipeline.run_from_request(
                validate_model(ResearchRequest, data, step="input", label=str(path))
            )
        else:
            raise PipelineError(
                "input", f"未対応の JSON です (type={kind!r})。speech_transcription か research_request を指定してください"
            )
    else:
        raise PipelineError("input", f"未対応のファイル形式です: {suffix} (.wav / .txt / .json に対応)")

    logger.info("完了しました")
    print()
    print(f"テーマ    : {result.report.topic}")
    print(f"PDF       : {result.pdf_path}")
    print(f"JSON      : {result.paths.json_dir}")
    print(f"出典数    : {len(result.report.sources)}")
    print(f"Discord   : {'送信済み' if result.sent_to_discord else '未送信'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except PipelineError as e:
        logger.error(e.describe())
        if args.verbose:
            logger.exception("詳細")
        return 1
    except KeyboardInterrupt:
        logger.error("中断されました")
        return 130


if __name__ == "__main__":
    sys.exit(main())
