"""STT 結果 (speech_transcription) のスキーマ。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TranscriptionSegment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    # faster-whisper が返す付加情報 (テキスト入力時などは None)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None


class TranscriptionSource(BaseModel):
    type: str = Field(default="cli", description="入力元 (m5stack / cli / http / text など)")
    audio_format: str | None = Field(default="wav", description="音声形式。テキスト入力時は None")
    filename: str | None = None


class TranscriptionBody(BaseModel):
    text: str
    segments: list[TranscriptionSegment] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("文字起こし結果が空です")
        return v


class TranscriptionModelInfo(BaseModel):
    name: str
    device: str
    compute_type: str


class SpeechTranscription(BaseModel):
    type: Literal["speech_transcription"] = "speech_transcription"
    version: str = "1.0"
    created_at: datetime
    language: str = "ja"
    language_probability: float | None = None
    duration_sec: float | None = None
    source: TranscriptionSource = Field(default_factory=TranscriptionSource)
    model: TranscriptionModelInfo | None = None
    transcription: TranscriptionBody
