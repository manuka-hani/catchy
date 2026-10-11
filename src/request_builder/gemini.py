"""Gemini Flash で STT 結果を構造化された調査依頼 (ResearchRequest) に変換する。

Gemini は「調査」はしない。発話の補正・整理と調査依頼への変換だけを担当する。
"""

import json
import logging
from datetime import datetime

from src.errors import PipelineError
from src.schemas import GeminiResearchDraft, ResearchRequest, inline_json_schema, validate_model
from src.schemas.research_request import OutputSpec, RequestInfo, ResearchSpec
from src.schemas.transcription import SpeechTranscription

logger = logging.getLogger(__name__)
STEP = "gemini"

SYSTEM_INSTRUCTION = """\
あなたは音声入力された「調査依頼」を整理するアシスタントです。
入力は日本語音声を faster-whisper で文字起こししたテキストで、誤認識・フィラー・言い淀みを含みます。

やること:
- 文脈から誤認識を可能な範囲で補正する (例: 「クロードコード」→「Claude Code」、「ジェミニ」→「Gemini」)
- 「えー」「あの」「えっと」などの不要な表現を除去し、話し言葉を整理する
- ユーザーが何を調べたいのかを抽出し、調査対象・観点を明確化する
- 日本語と英語の検索キーワード候補を作る
- 「最近」「今年」などの時間表現を time_range に解釈する (今日の日付: {today})
- 曖昧な発言も、可能な範囲で意味の通る調査依頼に変換する
- 比較の依頼であれば intent を comparison にし、comparison_targets に比較対象を列挙する

やらないこと:
- 実際の調査や回答そのもの (調査は後段の別エージェントが行う)
- 発話に存在しない要望の捏造

出力は指定された JSON スキーマに厳密に従ってください。
"""


def _build_client(api_key: str, timeout_sec: int):
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout_sec * 1000,
            retry_options=types.HttpRetryOptions(attempts=4, initial_delay=2.0, max_delay=30.0),
        ),
    )


def _call_gemini(client, model: str, transcription: SpeechTranscription) -> str:
    from google.genai import errors, types

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    payload = {
        "text": transcription.transcription.text,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in transcription.transcription.segments
        ],
    }
    prompt = (
        "以下は音声調査依頼の文字起こし結果 (JSON) です。調査依頼として構造化してください。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION.format(today=today),
                response_mime_type="application/json",
                response_json_schema=inline_json_schema(GeminiResearchDraft),
                temperature=0.2,
            ),
        )
    except errors.ClientError as e:
        if e.code == 429:
            raise PipelineError(
                STEP, f"Gemini API のレート制限に達しました: {e.message}",
                hint="しばらく待ってから再実行してください",
            ) from e
        if e.code in (401, 403) or "API key" in (e.message or ""):
            raise PipelineError(
                STEP, f"Gemini API の認証に失敗しました: {e.message}",
                hint="GEMINI_API_KEY が正しいか確認してください",
            ) from e
        if e.code == 404:
            raise PipelineError(
                STEP, f"Gemini モデル '{model}' が見つかりません: {e.message}",
                hint="GEMINI_MODEL を利用可能なモデル名に変更してください",
            ) from e
        raise PipelineError(STEP, f"Gemini API エラー ({e.code}): {e.message}") from e
    except errors.APIError as e:
        raise PipelineError(STEP, f"Gemini API エラー ({e.code}): {e.message}") from e
    except Exception as e:  # タイムアウト・ネットワークエラーなど
        raise PipelineError(STEP, f"Gemini API の呼び出しに失敗しました ({type(e).__name__}: {e})") from e

    text = response.text
    if not text:
        reason = None
        if response.candidates:
            reason = response.candidates[0].finish_reason
        raise PipelineError(STEP, f"Gemini から空の応答が返りました (finish_reason={reason})")
    return text


def draft_to_request(draft: GeminiResearchDraft, transcription: SpeechTranscription) -> ResearchRequest:
    return ResearchRequest(
        created_at=datetime.now().astimezone(),
        request=RequestInfo(
            original_transcription=transcription.transcription.text,
            corrected_transcription=draft.corrected_transcription,
            normalized_query=draft.normalized_query,
            intent=draft.intent,
        ),
        research=ResearchSpec(
            topic=draft.topic,
            scope=draft.scope,
            time_range=draft.time_range,
            time_range_note=draft.time_range_note,
            depth=draft.depth,
            search_targets=draft.search_targets,
            key_questions=draft.key_questions,
            comparison_targets=draft.comparison_targets,
            preferred_sources=draft.preferred_sources,
        ),
        output=OutputSpec(language=draft.output_language or "ja"),
    )


def build_research_request(
    transcription: SpeechTranscription, *, api_key: str, model: str, timeout_sec: int = 120, client=None
) -> ResearchRequest:
    """STT 結果から ResearchRequest を生成する。client はテスト用に差し替え可能。"""
    client = client or _build_client(api_key, timeout_sec)
    logger.info("Gemini (%s) で調査依頼を構造化しています...", model)

    raw = _call_gemini(client, model, transcription)
    logger.debug("Gemini raw response: %s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PipelineError(STEP, f"Gemini の応答が JSON として解析できません: {e}\n応答: {raw[:500]}") from e
    draft = validate_model(GeminiResearchDraft, data, step=STEP, label="Gemini の応答")

    if not draft.is_research_request:
        raise PipelineError(
            STEP,
            f"発話を調査依頼として解釈できませんでした: 「{transcription.transcription.text[:100]}」",
            hint="調べたいテーマをはっきり話して再度送信してください",
        )

    request = draft_to_request(draft, transcription)
    logger.info("調査依頼: %s (topic=%s)", request.request.normalized_query, request.research.topic)
    return request
