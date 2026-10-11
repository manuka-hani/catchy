"""パイプライン共通の例外。

どのステップで失敗したのかをログ・終了コードで判別できるよう、
すべての失敗は PipelineError(step=...) に包んで送出する。
"""


class PipelineError(Exception):
    """パイプラインのいずれかのステップで発生したエラー。"""

    def __init__(self, step: str, message: str, *, hint: str | None = None):
        self.step = step
        self.message = message
        self.hint = hint
        super().__init__(f"[{step}] {message}")

    def describe(self) -> str:
        text = f"ステップ '{self.step}' で失敗しました: {self.message}"
        if self.hint:
            text += f"\n  ヒント: {self.hint}"
        return text


class ConfigError(PipelineError):
    """APIキー未設定など、設定に起因するエラー。"""

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__("config", message, hint=hint)
