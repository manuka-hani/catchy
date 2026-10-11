"""Claude Agent SDK による自律 Web 調査。

ResearchRequest を受け取り、WebSearch / WebFetch を使って多段階に調査し、
構造化出力 (output_format=json_schema) で ResearchReport を返す。
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from src.errors import PipelineError
from src.schemas import ResearchReport, ResearchRequest, inline_json_schema, validate_model

from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)
STEP = "research"

RESEARCH_TOOLS = ["WebSearch", "WebFetch"]


class _Trace:
    """ツール呼び出しの記録 (デバッグ用に research_trace.jsonl へ保存する)。"""

    def __init__(self, path: Path | None):
        self.path = path
        self.calls: dict[str, dict] = {}
        self.ok = 0
        self.failed = 0
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def _write(self, record: dict) -> None:
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def tool_use(self, tool_id: str, name: str, tool_input: dict) -> None:
        self.calls[tool_id] = {"name": name, "input": tool_input}
        detail = tool_input.get("query") or tool_input.get("url") or ""
        logger.info("  -> %s %s", name, detail)
        self._write({"event": "tool_use", "id": tool_id, "name": name, "input": tool_input})

    def tool_result(self, tool_id: str, is_error: bool, content) -> None:
        call = self.calls.get(tool_id, {})
        if is_error:
            self.failed += 1
            logger.warning("  !! %s 失敗: %s", call.get("name", "?"), str(content)[:200])
        elif call.get("name") in RESEARCH_TOOLS:
            self.ok += 1
        self._write({"event": "tool_result", "id": tool_id, "is_error": bool(is_error)})


def _build_options(*, model, max_turns, max_budget_usd, work_dir: Path, output_schema: dict):
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        # 利用可能なツールを Web 調査用だけに絞り、許可プロンプトなしで使えるようにする
        tools=RESEARCH_TOOLS,
        allowed_tools=RESEARCH_TOOLS,
        permission_mode="dontAsk",
        # ユーザー/プロジェクトの settings.json や CLAUDE.md を読み込まない
        setting_sources=[],
        cwd=str(work_dir),
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_format={"type": "json_schema", "schema": output_schema},
        stderr=lambda line: logger.debug("[claude-cli] %s", line.rstrip()),
    )


def _raise_for_result(result) -> None:
    status = result.api_error_status
    errors = "; ".join(result.errors or []) or (result.result or "")
    if status == 429:
        raise PipelineError(STEP, f"Claude API のレート制限に達しました: {errors}",
                            hint="しばらく待ってから再実行してください")
    if status in (401, 403):
        raise PipelineError(STEP, f"Claude の認証に失敗しました: {errors}",
                            hint="ANTHROPIC_API_KEY を確認するか、`claude` CLI でログインしてください")
    if result.subtype == "error_max_turns":
        raise PipelineError(STEP, f"最大ターン数 ({result.num_turns}) に達し、レポートを完成できませんでした",
                            hint="CLAUDE_MAX_TURNS を増やして再実行してください")
    if result.subtype == "error_max_budget_usd":
        raise PipelineError(STEP, "予算上限 (CLAUDE_MAX_BUDGET_USD) に達しました",
                            hint="CLAUDE_MAX_BUDGET_USD を増やして再実行してください")
    if result.subtype == "error_max_structured_output_retries":
        raise PipelineError(STEP, "構造化レポートの出力に繰り返し失敗しました")
    raise PipelineError(STEP, f"Claude Agent SDK がエラーを返しました (subtype={result.subtype}, status={status}): {errors}")


async def _run(request: ResearchRequest, options, trace: _Trace):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage, query

    prompt = build_user_prompt(request, datetime.now().astimezone().strftime("%Y-%m-%d"))
    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            if message.error:
                logger.warning("Claude API エラー通知: %s", message.error)
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    trace.tool_use(block.id, block.name, block.input)
                elif isinstance(block, TextBlock) and block.text.strip():
                    logger.debug("Claude: %s", block.text.strip()[:300])
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    trace.tool_result(block.tool_use_id, bool(block.is_error), block.content)
        elif isinstance(message, ResultMessage):
            result = message
    return result


def _extract_report(result) -> dict:
    if result.structured_output is not None:
        data = result.structured_output
        return json.loads(data) if isinstance(data, str) else data
    # 念のため: 構造化出力が無い場合は最終テキストから JSON を探す
    text = (result.result or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise PipelineError(STEP, "Claude から構造化レポートが返りませんでした",
                        hint=f"最終応答: {text[:300]}")


def _check_citations(report: ResearchReport) -> None:
    ids = {s.id for s in report.sources}
    if not ids:
        logger.warning("レポートに出典が1件もありません")
    used = {i for st in report.important_facts + report.notable_opinions for i in st.source_ids}
    if missing := used - ids:
        logger.warning("存在しない出典番号が参照されています: %s", sorted(missing))


def run_research(
    request: ResearchRequest,
    *,
    work_dir: Path,
    trace_path: Path | None = None,
    model: str | None = None,
    max_turns: int = 80,
    max_budget_usd: float | None = None,
    timeout_sec: int = 2400,
) -> ResearchReport:
    try:
        import claude_agent_sdk as sdk
    except ImportError as e:
        raise PipelineError(STEP, "claude-agent-sdk がインストールされていません",
                            hint="pip install -r requirements.txt を実行してください") from e

    work_dir.mkdir(parents=True, exist_ok=True)
    options = _build_options(
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        work_dir=work_dir,
        output_schema=inline_json_schema(ResearchReport),
    )
    trace = _Trace(trace_path)

    logger.info("Claude Agent SDK で調査を開始します: %s", request.research.topic)
    try:
        result = asyncio.run(asyncio.wait_for(_run(request, options, trace), timeout=timeout_sec))
    except TimeoutError as e:
        raise PipelineError(STEP, f"調査が {timeout_sec} 秒以内に完了しませんでした (タイムアウト)",
                            hint="CLAUDE_TIMEOUT_SEC を増やすか、depth を下げてください") from e
    except sdk.CLINotFoundError as e:
        raise PipelineError(STEP, f"Claude Code CLI が見つかりません: {e}",
                            hint="claude-agent-sdk を再インストールしてください") from e
    except sdk.ProcessError as e:
        raise PipelineError(STEP, f"Claude Code プロセスが異常終了しました: {e}") from e
    except sdk.ClaudeSDKError as e:
        raise PipelineError(STEP, f"Claude Agent SDK エラー: {e}") from e

    if result is None:
        raise PipelineError(STEP, "Claude Agent SDK から結果メッセージが返りませんでした")
    logger.info(
        "調査完了: %dターン, Web取得成功 %d 回 / 失敗 %d 回, %.1f秒, コスト $%s",
        result.num_turns, trace.ok, trace.failed, result.duration_ms / 1000,
        f"{result.total_cost_usd:.3f}" if result.total_cost_usd is not None else "?",
    )
    if result.is_error:
        _raise_for_result(result)
    if trace.ok == 0:
        raise PipelineError(STEP, "Web 検索/取得が1回も成功しませんでした",
                            hint="ネットワーク接続、または WebSearch が利用可能な認証方式か確認してください")

    report = validate_model(ResearchReport, _extract_report(result), step=STEP, label="Claude のレポート出力")
    _check_citations(report)
    return report
