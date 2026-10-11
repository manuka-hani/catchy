# catchy 開発者ドキュメント

このドキュメントは、catchy (キャッチアップ専用リサーチガジェット) のコードを読む人・変更する人に向けたものです。
セットアップや使い方はトップの [README.md](../README.md) にあります。ここでは次の内容を扱います。

- 内部の設計
- 各ファイルの役割
- データ形式
- 処理の流れ
- 拡張するときの手順

---

## 目次

1. [全体像](#1-全体像)
2. [設計方針](#2-設計方針)
3. [ディレクトリ構成](#3-ディレクトリ構成)
4. [処理フロー詳細](#4-処理フロー詳細)
5. [データスキーマ](#5-データスキーマ)
6. [モジュール詳細](#6-モジュール詳細)
7. [設定 (環境変数)](#7-設定-環境変数)
8. [認証](#8-認証)
9. [エラーハンドリング](#9-エラーハンドリング)
10. [出力ファイルと再実行 (キャッシュ)](#10-出力ファイルと再実行-キャッシュ)
11. [ログ](#11-ログ)
12. [テスト](#12-テスト)
13. [拡張ガイド](#13-拡張ガイド)
14. [既存コード (app.py / features/) との関係](#14-既存コード-apppy--features-との関係)
15. [既知の制約・今後の課題](#15-既知の制約今後の課題)

---

## 1. 全体像

利用者が音声で投げた調査依頼を、次の 5 ステップで「出典付きの調査レポート PDF」にして Discord に届けます。

```text
          ┌──────────────── 入力インターフェース ────────────────┐
          │  CLI (src/main.py)   HTTP (app.py, 将来)   M5Stack  │
          └──────────────────────────┬──────────────────────────┘
                                     │ WAV / テキスト / JSON
                                     ▼
┌──────────────────────────── src/pipeline.py ─────────────────────────────┐
│                                                                          │
│ [1] STT          src/stt/             WAV ─▶ SpeechTranscription         │
│      faster-whisper (small/cpu/int8)        └▶ json/transcription.json   │
│                                                                          │
│ [2] 依頼の構造化  src/request_builder/  SpeechTranscription ─▶ ResearchRequest │
│      Gemini Flash (JSON Schema 出力)        └▶ json/research_request.json│
│                                                                          │
│ [3] Web 調査     src/research/        ResearchRequest ─▶ ResearchReport  │
│      Claude Agent SDK                       └▶ json/research_report.json │
│      (WebSearch / WebFetch のみ)            └▶ json/research_trace.jsonl │
│                                                                          │
│ [4] PDF 生成     src/pdf/             ResearchReport ─▶ PDF              │
│      fpdf2 + 日本語 TTF                     └▶ pdf/research_report.pdf   │
│                                                                          │
│ [5] 通知         src/notify/          PDF ─▶ Discord Webhook             │
└──────────────────────────────────────────────────────────────────────────┘
```

各ステップの間は Pydantic モデル (`src/schemas/`) を JSON にしたものだけで受け渡します。
そのため、どのステップも単独で差し替えたり、単独で実行したりできます。

---

## 2. 設計方針

| 方針 | 実装上の現れ |
|---|---|
| 責務の分離 | 1 ステップ = 1 パッケージにしています。ステップ同士は互いを import しません。ステップをつなぐのは `pipeline.py` だけです |
| 疎結合 | ステップ間の受け渡しは `src/schemas/` の型だけです。PDF 生成は Claude のことを知らず、`ResearchReport` だけを受け取ります |
| 入力経路に依存しない | `Pipeline.run_from_audio / run_from_text / run_from_transcription / run_from_request` の 4 つを入口として用意しています。CLI・HTTP・M5Stack のどこからでも呼べます |
| 秘密情報をコードに書かない | 秘密情報は `src/config.py` で `.env` と環境変数から読み込みます。`.env` は `.gitignore` に入っています |
| 失敗箇所が分かること | すべての失敗を `PipelineError(step=...)` に包んで送出します。CLI はステップ名とヒントを表示し、終了コード 1 で終わります |
| 高い処理を繰り返さない | 各ステップの結果を JSON に保存し、再実行時はそれを再利用します |
| LLM に任せる範囲を最小にする | Gemini には意味解釈だけを任せます。原文・日時・型情報はプログラム側で埋めます。Claude が使えるツールは Web 系だけに絞っています |

---

## 3. ディレクトリ構成

```text
catchy/
├── README.md                  利用者向けの説明 (セットアップと使い方)
├── docs/
│   └── DEVELOPMENT.md         このファイル
├── .env.example               環境変数のひな形。コピーして .env を作る
├── .env                       実際の秘密情報 (Git の管理対象外)
├── .gitignore                 .env / output/ / .venv/ / WAV などを除外する
├── requirements.txt           依存ライブラリ。既存サーバー用と src/ 用の両方を含む
│
├── app.py                     【既存】M5Stack からの WAV を受け取る Flask サーバー
├── features/
│   └── speech_to_text.py      【既存】WAV を TXT に保存する文字起こし (app.py が使う)
├── Dockerfile                 【既存】app.py 用。src/ は含まない
├── docker-compose.yml         【既存】app.py 用
│
├── src/                       調査パイプライン本体
│   ├── main.py                CLI エントリポイント (python -m src.main)
│   ├── pipeline.py            ステップの実行順序・保存・再利用
│   ├── config.py              Settings (.env 読み込み) とログ設定
│   ├── errors.py              PipelineError / ConfigError
│   │
│   ├── schemas/               ステップ間で受け渡すデータ型 (Pydantic)
│   │   ├── __init__.py        検証・保存・読込のヘルパーと JSON Schema の展開
│   │   ├── transcription.py   SpeechTranscription (STT 結果)
│   │   ├── research_request.py ResearchRequest (調査依頼) / GeminiResearchDraft
│   │   └── report.py          ResearchReport (調査結果)
│   │
│   ├── stt/
│   │   └── transcriber.py     faster-whisper による文字起こし、テキスト入力の変換
│   ├── request_builder/
│   │   └── gemini.py          Gemini で発話を ResearchRequest に変換
│   ├── research/
│   │   ├── agent.py           Claude Agent SDK の実行、結果の検証、トレース
│   │   └── prompts.py         システムプロンプト (調査方針) とユーザープロンプト
│   ├── pdf/
│   │   ├── generator.py       ResearchReport から PDF へのレイアウト
│   │   └── fonts.py           日本語フォントの探索
│   └── notify/
│       └── discord.py         Discord Webhook への添付送信 (再試行あり)
│
├── tests/
│   ├── conftest.py            共通フィクスチャ (テスト用の Settings、サンプル JSON)
│   ├── fixtures/              サンプルの依頼文・依頼 JSON・レポート JSON
│   ├── test_schemas.py
│   ├── test_gemini.py
│   ├── test_pdf_and_discord.py
│   └── test_pipeline.py       パイプライン全体と CLI
│
├── input_audio/               テスト用 WAV の置き場 (*.wav は Git の管理対象外)
└── output/                    実行結果 (Git の管理対象外)。構成は 10 章を参照
```

各パッケージの `__init__.py` は、外から使う関数だけを再エクスポートしています。
例: `from src.stt import transcribe, from_text`。

---

## 4. 処理フロー詳細

### 4.1 CLI から各ステップまで

`python -m src.main <input>` を実行すると、次の順に処理が進みます。

1. **`src/main.py: main()`**
   1. 引数を解析し、`setup_logging()` を呼びます
   2. `run()` を呼び、`PipelineError` を捕捉したらステップ名付きで表示して終了コード 1 を返します
2. **`src/main.py: run()`**
   1. `load_settings()` で `.env` と環境変数を読みます
   2. 入力ファイルの存在を確認し、`Pipeline(settings, run_name, resume, send_discord)` を作ります。`run_name` の既定値は入力ファイル名 (拡張子なし) です
   3. 拡張子で入口を選びます

      | 入力 | 呼ぶメソッド | 省略されるステップ |
      |---|---|---|
      | `.wav .mp3 .m4a .flac .ogg` | `run_from_audio()` | なし |
      | `.txt` | `run_from_text()` | STT |
      | `.json` (`"type": "speech_transcription"`) | `run_from_transcription()` | STT |
      | `.json` (`"type": "research_request"`) | `run_from_request()` | STT と Gemini |

3. **`Pipeline` の各入口**は、上流から下流へ順に次の入口を呼びます。

   ```text
   run_from_audio ─▶ run_from_transcription ─▶ run_from_request ─▶ PDF ─▶ Discord
   run_from_text  ─┘
   ```

   各ステップの前に `_cached()` を呼び、保存済みの JSON があれば使います (10 章)。

### 4.2 事前チェック (`Pipeline._preflight`)

Whisper の実行には数十秒かかります。そのあとで「API キーが無い」と分かると時間が無駄になるので、最初に次の 2 点を確認します。

- Gemini を呼ぶ予定があるとき (`research_request.json` が無いとき) は、`GEMINI_API_KEY` があるか
- Discord に送るとき (`--no-discord` が無いとき) は、`DISCORD_WEBHOOK_URL` があるか

不足していれば、`ConfigError` (step=`config`) で即座に止めます。

### 4.3 各ステップの内容

| # | 関数 | 入力 | 出力 | 外部依存 |
|---|---|---|---|---|
| 1 | `stt.transcribe()` | WAV のパス | `SpeechTranscription` | faster-whisper (ローカルの CPU) |
| 1' | `stt.from_text()` | 文字列 | `SpeechTranscription` (segments は空) | なし |
| 2 | `request_builder.build_research_request()` | `SpeechTranscription` | `ResearchRequest` | Gemini API |
| 3 | `research.run_research()` | `ResearchRequest` | `ResearchReport` | Claude Agent SDK (同梱の CLI をサブプロセスとして起動) |
| 4 | `pdf.generate_pdf()` | `ResearchReport` (+ 表紙用の `ResearchRequest`) | PDF ファイル | fpdf2、OS のフォント |
| 5 | `notify.send_pdf()` | PDF とメッセージ | なし | Discord Webhook |

---

## 5. データスキーマ

すべて `src/schemas/` に Pydantic v2 モデルとして定義しています。
JSON ファイルはこれらのモデルを `model_dump(mode="json")` で書き出したものです。

### 5.1 SpeechTranscription (`transcription.json`)

```jsonc
{
  "type": "speech_transcription",      // 固定値。.json 入力の種別判定に使う
  "version": "1.0",
  "created_at": "2026-10-12T02:24:44+09:00",
  "language": "ja",
  "language_probability": 1.0,         // Whisper の言語判定の確度
  "duration_sec": 6.59,                // 音声の長さ
  "source": {
    "type": "m5stack",                 // 入力元: cli / m5stack / http / text
    "audio_format": "wav",             // テキスト入力のときは null
    "filename": "sample.wav"
  },
  "model": { "name": "small", "device": "cpu", "compute_type": "int8" },
  "transcription": {
    "text": "AIエージェントについて…",   // セグメントを連結した全文。空文字は検証エラー
    "segments": [
      {
        "start": 0.0, "end": 4.2, "text": "…",
        "avg_logprob": -0.21,          // 認識の確からしさ (デバッグ用)
        "no_speech_prob": 0.01,
        "compression_ratio": 1.1
      }
    ]
  }
}
```

### 5.2 ResearchRequest (`research_request.json`)

パイプラインの**中心となる共通インターフェース**です。どの入口から来た依頼も、最終的にこの形になってから調査に渡ります。

```jsonc
{
  "type": "research_request",
  "version": "1.0",
  "created_at": "…",
  "request": {
    "original_transcription": "Aと、AIエジェントについて…",  // STT の原文 (プログラムが埋める)
    "corrected_transcription": "AIエージェントについて…",    // Gemini が補正した文
    "normalized_query": "AIエージェントに関する最新動向を調査する",
    "intent": "research"        // research | comparison | catch_up | how_to | other
  },
  "research": {
    "topic": "AIエージェント",
    "scope": "最新動向",
    "time_range": "recent",     // recent | past_week | past_month | past_year | all_time | custom
    "time_range_note": null,    // custom のときの補足 (例: "2026年以降")
    "depth": "comprehensive",   // quick | standard | comprehensive
    "search_targets": ["AI agent", "AIエージェント", "agentic AI"],
    "key_questions": ["…"],     // 調査で答えるべき問い
    "comparison_targets": [],   // 比較依頼のときの比較対象
    "preferred_sources": []     // 利用者が指定した情報源 (X, Reddit など)
  },
  "output": { "format": "pdf", "language": "ja" }
}
```

`depth` によって、Claude に指示する調査量が変わります (`research/prompts.py`)。

| depth | 指示の目安 |
|---|---|
| quick | 検索 5〜10 回程度 |
| standard | 検索 10〜20 回程度 |
| comprehensive | 検索 20 回以上。重要なページは本文まで確認する |

### 5.3 GeminiResearchDraft (Gemini の出力専用)

`ResearchRequest` のうち、**LLM に判断させる部分だけ**を切り出したモデルです (`research_request.py`)。

- `type`、`version`、`created_at`、`original_transcription` は Gemini に作らせません。改変されたり幻覚が入ったりすると困るためです
- `is_research_request` を返させます。false のとき (雑音や無関係な発話) は step=`gemini` のエラーで止めます
- すべてのフィールドを必須にしています (null を許すフィールドも必須)。Gemini が項目を省略しないようにするためです。テスト `test_gemini_draft_schema_all_required` でこれを保証しています

`gemini.draft_to_request()` が `GeminiResearchDraft` と `SpeechTranscription` を組み合わせて `ResearchRequest` を作ります。

### 5.4 ResearchReport (`research_report.json`)

Claude Agent SDK に構造化出力として返させる調査結果です。PDF はこのモデルだけから作ります。

| フィールド | 内容 |
|---|---|
| `title`, `topic` | レポートの題名とテーマ |
| `overview` | 調査概要 (何を、どの範囲で、どう調べたか) |
| `executive_summary` | 結論の要約。Discord のメッセージにも使う |
| `key_findings[]` | 主要な発見。各項目に `[n]` で出典番号を付ける |
| `sections[]` | 詳細調査の章 (`heading`、`paragraphs[]`、`bullets[]`)。章の構成はテーマごとに Claude が決める |
| `important_facts[]`, `notable_opinions[]` | `ClassifiedStatement` = `statement` + `classification` (FACT / OPINION / USER_REPORT / INFERENCE) + `source_ids[]` |
| `analysis`, `conclusion` | 分析・考察 (公式発表とユーザー反応の比較を含む) と結論 |
| `sources[]` | `Source` = `id`、`title`、`url`、`source_type`、`publisher`、`published_date` (null 可)、`accessed_date`、`reliability` (high / medium / low) |
| `research_notes` | 実際に使った検索クエリ (`search_queries`) と調査の限界 (`limitations`) |

`source_type` に指定できる値は `official_docs, official_blog, press_release, github, paper, news, tech_blog, x, reddit, qiita, zenn, hacker_news, stack_overflow, other` です。

### 5.5 スキーマ関連のヘルパー (`src/schemas/__init__.py`)

| 関数 | 役割 |
|---|---|
| `validate_model(model, data, step, label)` | dict や JSON 文字列を検証します。失敗すると、どのフィールドが何で不正かを列挙した `PipelineError` を送出します |
| `load_model(model, path, step)` | ファイルを読み込んで検証します |
| `save_model(obj, path)` | UTF-8 で `ensure_ascii=False, indent=2` の JSON として保存します |
| `inline_json_schema(model)` | `$defs` と `$ref` を展開し、注釈用の `title` を除いた JSON Schema を返します。Gemini と Claude の構造化出力に渡します。`title` という**名前のプロパティ**は残します |

---

## 6. モジュール詳細

### 6.1 `src/main.py`: CLI

- 引数
  - `input` (必須)
  - `--run-name`: 出力フォルダ名
  - `--source`: 入力元。`transcription.json` の `source.type` に記録します
  - `--fresh`: キャッシュを使いません
  - `--no-discord`
  - `--output-dir`
  - `-v/--verbose`
- 終了コード: 成功は `0`、`PipelineError` は `1`、Ctrl+C は `130`
- 完了したら、テーマ、PDF のパス、JSON フォルダ、出典数、Discord への送信有無を表示します

### 6.2 `src/pipeline.py`: オーケストレーション

- **`RunPaths`**: `output/<run_name>/` の下にある各ファイルのパスをまとめたものです。パスはすべてここで決めています
- **`Pipeline`**
  - コンストラクタは `settings`、`run_name`、`resume=True`、`send_discord=True` を受け取ります
  - 入口は 4 つです (4.1 節)。引数の `_checked` は、上流の入口から呼ばれて事前チェックが済んでいるかどうかを示す内部フラグです
  - **PDF 生成と Discord 送信はキャッシュしません。** 毎回実行するので、レイアウトを直したときや送信に失敗したときは、同じコマンドを再実行するだけで済みます
- **`PipelineResult`**: `paths`、`request`、`report`、`pdf_path`、`sent_to_discord` を持ちます
- テストでは `src.pipeline` モジュールの `transcribe`、`build_research_request`、`run_research`、`send_pdf` を monkeypatch で差し替えています。そのため、これらは `from ... import 関数` の形で import しています

### 6.3 `src/stt/transcriber.py`: 音声認識

- `get_model(name, device, compute_type)`
  - `WhisperModel` をプロセス内でキャッシュします (キーは 3 つの設定値の組)
  - スレッドロックで保護しているので、HTTP サーバーから並行して呼ばれても安全です
  - 初回は Hugging Face からモデル (`Systran/faster-whisper-small`) をダウンロードします
- `transcribe(wav_path, source_type, model_name, device, compute_type, language="ja")`
  1. `_check_audio_file()` で、ファイルが存在するか、ファイルかどうか、44 バイト以下 (WAV ヘッダのみ) でないかを確認します
  2. `model.transcribe(..., language="ja", vad_filter=True)` を呼びます。VAD で無音区間を除くので、M5Stack の録音前後の無音に強くなります
  3. segments はジェネレータなので、リスト化した時点で実際のデコードが走ります。壊れた WAV の例外 (`av.error.InvalidDataError` など) はここで発生するため、ここで捕捉しています
  4. 全文が空なら「発話を検出できませんでした」で止めます
- `from_text(text, source_type="text", filename=None)`
  - テキスト入力を `SpeechTranscription` に包みます。segments は空、`audio_format` は null です

### 6.4 `src/request_builder/gemini.py`: 調査依頼の構造化

- `SYSTEM_INSTRUCTION` では次の内容を指示しています
  - やること: 誤認識の補正、フィラーの除去、意図の抽出、検索語を日英で作る、時間表現の解釈 (今日の日付を埋め込む)、比較対象の抽出
  - やらないこと: 調査そのもの、発話にない要望の捏造
- `_build_client()` は `google.genai.Client` を作ります。設定内容は次のとおりです
  - `HttpOptions.timeout`: `GEMINI_TIMEOUT_SEC` をミリ秒にした値
  - `HttpRetryOptions`: 最大 4 回、2 秒から始めて最大 30 秒まで間隔を延ばす。429 や 5xx は SDK が自動で再試行します
- `_call_gemini()` は `client.models.generate_content()` を次の設定で呼びます
  - `response_mime_type="application/json"`
  - `response_json_schema=inline_json_schema(GeminiResearchDraft)`
  - `temperature=0.2`
  - 入力には全文に加えてセグメントの時刻付きテキストも JSON で渡します
- エラーの対応は次のとおりです

  | 例外や状況 | メッセージ・ヒント |
  |---|---|
  | `ClientError` 429 | レート制限。しばらく待つよう案内します |
  | `ClientError` 401 / 403、またはメッセージに "API key" を含む | 認証失敗。`GEMINI_API_KEY` の確認を案内します |
  | `ClientError` 404 | モデルが見つからない。`GEMINI_MODEL` の変更を案内します |
  | その他の `APIError` | HTTP コードとメッセージをそのまま表示します |
  | タイムアウトやネットワークエラー | 例外の型名とメッセージを表示します |
  | 応答が空 | `finish_reason` を表示します (安全フィルタなど) |
  | JSON として解析できない、またはスキーマ違反 | 応答の先頭 500 文字、または違反しているフィールドを表示します |
- `build_research_request(..., client=None)` の `client` には、テストのときだけ偽のクライアントを渡します

### 6.5 `src/research/`: Claude Agent SDK による調査

#### `prompts.py`

- `SYSTEM_PROMPT`: 調査エージェントの行動規範です
  - 10 段階の調査手順 (仕様書 9 章)
  - 重視する情報源と、`site:` 演算子の活用
  - SNS からは反応・利用例・不満・失敗例を抽出し、公式発表と比較すること
  - 信頼性 (high / medium / low) と、FACT / OPINION / USER_REPORT / INFERENCE への分類
  - 出典のルール: 本文に `[n]` を付ける、実際に開いた URL だけを使う、URL を推測で作らない
  - 章構成は柔軟に決めること。見出しには番号を付けないこと
- `build_user_prompt(request, today)`: 今日の日付、`depth` に応じた調査量の目安、`ResearchRequest` の JSON を渡します

**調査の質を上げたいときは、まずこのファイルを調整してください。**

#### `agent.py`

- `_build_options()` が作る `ClaudeAgentOptions` は次のとおりです

  | オプション | 値 | 理由 |
  |---|---|---|
  | `system_prompt` | `SYSTEM_PROMPT` | Claude Code の既定プロンプト (コーディング向け) を使わない |
  | `tools` / `allowed_tools` | `["WebSearch", "WebFetch"]` | 使えるツールを Web 調査用だけに限定する |
  | `permission_mode` | `"dontAsk"` | 許可済みのツールは確認なしで使い、それ以外は拒否する |
  | `setting_sources` | `[]` | 利用者やプロジェクトの `settings.json` / `CLAUDE.md` / スキルを読み込まない (再現性と安全のため) |
  | `cwd` | `output/<run>/work` | 作業ディレクトリを実行ごとに分ける |
  | `model` / `max_turns` / `max_budget_usd` | 環境変数の値 | コストと時間の上限 |
  | `output_format` | `{"type": "json_schema", "schema": ResearchReport のスキーマ}` | 最終出力を構造化 JSON にする |
  | `stderr` | DEBUG ログに流す | `-v` を付けたときに CLI のログが見える |

- `_run()`: `claude_agent_sdk.query()` のメッセージストリームを読みます
  - `AssistantMessage` の `ToolUseBlock` は、検索クエリや URL を INFO ログに出してトレースに記録します
  - `UserMessage` の `ToolResultBlock` は、成功と失敗を数えます
  - 最後に届く `ResultMessage` を返り値にします
- `run_research()` の流れは次のとおりです
  1. `asyncio.run(asyncio.wait_for(_run(...), timeout=CLAUDE_TIMEOUT_SEC))` を実行します
  2. SDK の例外 (`CLINotFoundError`、`ProcessError`、`ClaudeSDKError`) とタイムアウトを `PipelineError` に変換します
  3. 所要ターン数、Web 取得の成功数と失敗数、秒数、コストをログに出します
  4. `result.is_error` なら `_raise_for_result()` で原因別のメッセージを出します。対象は 429、401 / 403、`error_max_turns`、`error_max_budget_usd`、`error_max_structured_output_retries`、その他です
  5. **Web 検索や取得が 1 回も成功していなければエラーにします。** 調べずに書いたレポートを防ぐためです
  6. `_extract_report()` は `result.structured_output` を取り出します。無い場合は最終テキストから JSON を探します
  7. `validate_model(ResearchReport, ...)` で検証します
  8. `_check_citations()` で、存在しない出典番号への参照があれば警告します (エラーにはしません)
- `_Trace` はツール呼び出しを `research_trace.jsonl` に 1 行 1 イベントで書き出します

  ```jsonc
  {"event": "tool_use", "id": "toolu_…", "name": "WebSearch", "input": {"query": "…"}}
  {"event": "tool_result", "id": "toolu_…", "is_error": false}
  ```

### 6.6 `src/pdf/`: PDF 生成

#### `fonts.py`

`find_japanese_font(regular, bold)` は次の順でフォントを決めます。

1. `PDF_FONT_PATH` / `PDF_BOLD_FONT_PATH` が指定されていればそれを使います。通常体のパスが存在しなければエラーにします
2. 指定が無ければ、次の候補を上から順に探します

   | OS | 候補 |
   |---|---|
   | Windows | 游ゴシック (`YuGothM/B.ttc`)、BIZ UDゴシック、メイリオ、MS ゴシック |
   | Linux | Noto Sans CJK、IPA ゴシック |
   | macOS | ヒラギノ角ゴシック |

3. 太字が無い場合は通常体で代用します。どれも見つからなければ step=`pdf` のエラーにします

BIZ UDゴシック (の ttc の 0 番) は等幅で、英字が読みにくくなります。そのため、プロポーショナルな游ゴシックを優先しています。

#### `generator.py`

- `_Writer` は fpdf2 の薄いラッパーで、次のメソッドを持ちます
  - `text()`: すべて `WrapMode.CHAR` で折り返します。日本語には空白がないので、文字単位で折り返す必要があります
  - `heading()`: 大見出しは青字と下線、小見出しは黒の太字です。ページの下端に近いときは改ページします
  - `paragraph()`, `bullets()`
  - `tagged()`: `[事実]` などの分類ラベル付きの項目
- `_clean()` は Markdown の強調記号 (`**`、`__`、`` ` ``) を除きます。LLM が混ぜてくるためです
- 小見出しは、LLM が付けた番号 (`1.`、`第1章` など) を正規表現で除いてから振り直します
- PDF の構成: 表紙ヘッダ (テーマ、依頼内容、調査日時、出典数) → 調査概要 → 結論 / Executive Summary → 主要な発見 → 詳細調査 → 重要な事実 → 注目すべき意見 → 分析・考察 → 結論 → 参考資料・出典 (URL にはリンクを付ける) → 調査メモ
- フッターにはページ番号を `n / 全体` の形で入れます
- fpdf2 の例外はすべて step=`pdf` の `PipelineError` に変換します

### 6.7 `src/notify/discord.py`: Discord 送信

- `build_message()`
  - 「調査完了 / テーマ / 調査日時 / レポート / 要約」の本文を作ります
  - Discord の上限は 2000 文字なので、1900 文字で切り詰めます
- `send_pdf()`
  - 事前チェック: URL があるか、`https://` で始まるか、PDF が存在するか、10MB 以下か
  - `multipart/form-data` で送ります。`payload_json` には本文、`attachments`、`allowed_mentions: {parse: []}` (メンション無効) を入れ、`files[0]` に PDF を入れます。`?wait=true` を付けて送ります
  - 応答ごとの処理は次のとおりです。最大 4 回試行します

    | 応答 | 処理 |
    |---|---|
    | 2xx | 成功 |
    | 429 | `retry_after` 秒待って再試行 |
    | 401 / 403 / 404 | 「Webhook が無効」として即座に失敗 |
    | その他の 4xx | 即座に失敗 |
    | 5xx、タイムアウト、通信エラー | 2^n 秒待って再試行 |

### 6.8 `src/config.py` / `src/errors.py`

- `load_settings(env_file=None)`
  - プロジェクト直下の `.env` を `override=False` で読み込みます。つまり、既に設定されている環境変数のほうが優先されます
  - 読み込んだ値から `Settings` (frozen な dataclass) を作ります
  - 数値の環境変数が不正な値なら `ConfigError` にします
- `Settings.require_gemini()` / `require_discord()`: 必要な値が無ければ `ConfigError` にします
- `setup_logging(verbose)`
  - stdout と stderr を UTF-8 にします。Windows の cp932 コンソールでも文字化けや例外が出ないようにするためです
  - 外部ライブラリの冗長なログは WARNING 以上に絞ります
- `PipelineError(step, message, hint=None)`: `describe()` で「ステップ 'x' で失敗しました: … / ヒント: …」の文字列を作ります
- `ConfigError`: step が `config` に固定された `PipelineError` です

---

## 7. 設定 (環境変数)

| 変数 | 既定値 | 使う場所 | 説明 |
|---|---|---|---|
| `GEMINI_API_KEY` | (必須) | request_builder | Google AI Studio で発行するキー |
| `GEMINI_MODEL` | `gemini-3.8-flash` | request_builder | 使う Flash 系のモデル名 |
| `GEMINI_TIMEOUT_SEC` | `120` | request_builder | 1 リクエストあたりのタイムアウト |
| `DISCORD_WEBHOOK_URL` | (必須*) | notify | `--no-discord` のときは不要 |
| `ANTHROPIC_API_KEY` | 空 | research (SDK が読む) | API 従量課金で使う場合に設定する。8 章を参照 |
| `CLAUDE_CODE_OAUTH_TOKEN` | 空 | research (SDK が読む) | サブスク枠で動かすための長期トークン。8 章を参照 |
| `CLAUDE_MODEL` | 空 (CLI の既定) | research | 使う Claude のモデル |
| `CLAUDE_MAX_TURNS` | `80` | research | エージェントループの最大ターン数 |
| `CLAUDE_MAX_BUDGET_USD` | 空 (無制限) | research | 1 回の調査の費用上限 |
| `CLAUDE_TIMEOUT_SEC` | `2400` | research | 調査全体のタイムアウト |
| `WHISPER_MODEL` | `small` | stt | `tiny / base / small / medium / large-v3` など |
| `WHISPER_DEVICE` | `cpu` | stt | GPU を使う場合は `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | stt | GPU を使う場合は `float16` など |
| `PDF_FONT_PATH` / `PDF_BOLD_FONT_PATH` | 空 (自動検出) | pdf | 日本語フォントのパス |
| `OUTPUT_DIR` | `./output` | pipeline | 出力のルートディレクトリ |

設定項目を追加するときは、次の 4 か所をそろえて更新してください。

1. `Settings` にフィールドを追加する
2. `load_settings()` で読み込む
3. `.env.example` に追記する
4. この表と README に追記する

---

## 8. 認証

### Gemini / Discord

`.env` の `GEMINI_API_KEY` と `DISCORD_WEBHOOK_URL` を、アプリが直接読みます。

### Claude Agent SDK

Agent SDK は同梱の Claude Code CLI をサブプロセスとして起動します。認証は CLI が環境変数とログイン情報から解決します。
アプリ側で認証を扱うコードはありません。`load_dotenv()` が `.env` の値を `os.environ` に入れ、子プロセスがそれを引き継ぐだけです。

CLI は次の優先順で認証情報を選びます (抜粋)。

1. `ANTHROPIC_API_KEY`: API の従量課金
2. `CLAUDE_CODE_OAUTH_TOKEN`: `claude setup-token` で発行する 1 年有効のトークン。Pro / Max のサブスク枠を使う
3. `claude` で `/login` したときの保存済み認証情報 (`~/.claude/.credentials.json`)

| 実行環境 | 推奨する設定 |
|---|---|
| 手元の PC (`claude` でログイン済み) | 何も設定しなくてよい (3 が使われる) |
| サーバー、Docker、CI (サブスク枠で動かす) | 手元で `claude setup-token` を実行し、表示されたトークンをサーバーの `.env` に `CLAUDE_CODE_OAUTH_TOKEN=...` として書く。`ANTHROPIC_API_KEY` の行は消す (残っているとそちらが優先される) |
| 従量課金で動かす | `ANTHROPIC_API_KEY` を設定する |

サブスク認証は、個人用ツールとして使う前提のものです。
このガジェットを他の人にも使わせる場合は、API キー方式に切り替えてください。
Agent SDK のドキュメントでは、第三者向けの製品で claude.ai ログインを提供することは、承認がない限り禁止されています。

---

## 9. エラーハンドリング

### 9.1 方針

- 各モジュールは、外部ライブラリの例外を**そのモジュールの中で** `PipelineError(step=...)` に変換します。上位層は `PipelineError` だけを扱えば済みます
- メッセージは「何が起きたか」を書き、`hint` に「次に何をすればよいか」を書きます
- 元の例外は `raise ... from e` で残します。`-v` を付けるとスタックトレースまで表示されます

### 9.2 step の一覧

| step | 発生元 | 主な原因 |
|---|---|---|
| `input` | main.py | 入力ファイルが無い、未対応の拡張子、未対応の JSON の種類、JSON の構文エラー、スキーマ違反 |
| `config` | config.py / pipeline.py | API キーや Webhook が未設定、数値の環境変数が不正 |
| `stt` | stt/ | WAV が無い、空、または壊れている。モデルのロード失敗。発話が無い |
| `gemini` | request_builder/ | 認証、レート制限、モデル名の誤り、タイムアウト、不正な JSON、スキーマ違反、調査依頼ではない発話 |
| `research` | research/ | CLI が無い、プロセスの異常終了、タイムアウト、レート制限、認証、ターン上限、予算上限、構造化出力の失敗、Web 取得が 0 件、スキーマ違反 |
| `pdf` | pdf/ | フォントが無い、fpdf2 の例外 |
| `discord` | notify/ | Webhook が未設定、無効、または形式が不正。PDF が無い、または 10MB 超。タイムアウト、レート制限、5xx (再試行してもだめだった場合) |

### 9.3 表示例

```text
02:31:12 [ERROR] src.main: ステップ 'gemini' で失敗しました: Gemini API の認証に失敗しました: API key not valid. …
  ヒント: GEMINI_API_KEY が正しいか確認してください
```

---

## 10. 出力ファイルと再実行 (キャッシュ)

```text
output/<run_name>/
├── json/
│   ├── transcription.json      [1] の結果。Whisper が何を認識したか
│   ├── research_request.json   [2] の結果。Gemini がどう解釈したか
│   ├── research_report.json    [3] の結果。Claude の構造化レポート
│   └── research_trace.jsonl    [3] で実行した検索・取得の記録
├── pdf/
│   └── research_report.pdf     [4] の結果
└── work/                       Claude Agent SDK の作業ディレクトリ
```

### 再実行のルール (`Pipeline._cached`)

- `resume=True` (既定) で JSON が存在する場合は、読み込んで検証し、そのステップを飛ばします
- JSON が壊れていたりスキーマに合わなかったりする場合は、警告を出してそのステップをやり直します
- `--fresh` を付けると `resume=False` になり、全ステップをやり直します
- PDF と Discord は常に実行します
- `run_name` の既定値は入力ファイル名です。同じファイル名で中身が違う音声を処理する場合は、`--run-name` で名前を変えるか `--fresh` を付けてください

### よく使う操作

```bash
# 調査結果はそのままで、PDF のレイアウトだけ作り直す (Claude を呼ばない)
python -m src.main input_audio/sample.wav --no-discord

# Gemini の解釈だけやり直す
rm output/sample/json/research_request.json output/sample/json/research_report.json
python -m src.main input_audio/sample.wav

# 調査依頼を手で書き換えて調査し直す
#   research_request.json を編集し、research_report.json を削除してから再実行する
```

---

## 11. ログ

- 形式は `HH:MM:SS [LEVEL] logger名: メッセージ` です
- INFO では次のような内容が出ます
  - ステップ番号 (`[1/5] 音声認識 …`)
  - 文字起こしの全文
  - 調査依頼の要約
  - Claude が実行した検索クエリや取得 URL (`-> WebSearch …`)
  - 調査の統計 (ターン数、成功数と失敗数、所要時間、コスト)
  - 保存したファイルのパス
- `-v` を付けると、次の内容も出ます
  - Gemini の生の応答
  - Claude の途中のテキスト
  - Claude CLI の stderr (`[claude-cli]`)
  - fpdf2 の詳細ログ
  - 失敗時のスタックトレース

---

## 12. テスト

```bash
python -m pytest            # 全テスト (外部 API は呼ばない。数秒で終わる)
python -m pytest -k gemini  # 一部だけ実行する
```

| ファイル | 内容 |
|---|---|
| `test_schemas.py` | フィクスチャの妥当性、検証エラーの読みやすさ、空の文字起こしを拒否するか、仕様書の JSON 例との互換、スキーマ展開 (`$ref` が無いこと、`title` プロパティが残ること) |
| `test_gemini.py` | 偽クライアントでの正常系 (比較依頼)、不正な JSON、スキーマ違反、調査依頼ではない発話、429 / 403 / 404、タイムアウト |
| `test_pdf_and_discord.py` | 日本語フォントが埋め込まれた PDF の生成、フォント指定の誤り、Discord の成功、Webhook 未設定、無効な Webhook、429 からの再試行、タイムアウト、メッセージの切り詰め |
| `test_pipeline.py` | 全体フロー、途中結果の再利用、`--fresh`、キー未設定時に STT 前で止まること、Webhook 未設定と `--no-discord`、依頼 JSON からの入力、壊れたキャッシュの再生成、CLI (WAV が無い、キーが無い、テキスト入力、未対応の JSON) |

### テストの方針

- 外部 API を呼ぶ関数は `src.pipeline` の名前空間で monkeypatch するか、`client=` 引数で差し替えます
- `conftest.py` の `settings` フィクスチャは、実環境の `.env` や環境変数を無視し、`tmp_path` に出力します

### 手動の結合テスト (実際の API を使う)

1. **Windows で WAV を作る**: 音声合成 (Microsoft Haruka) で `tests/fixtures/*.txt` を読み上げて保存します

   ```powershell
   Add-Type -AssemblyName System.Speech
   $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
   $s.SelectVoice("Microsoft Haruka Desktop")
   $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
   $s.SetOutputToWaveFile("input_audio\specific.wav", $fmt)
   $s.Speak((Get-Content tests\fixtures\specific.txt -Encoding UTF8 -Raw)); $s.Dispose()
   ```

2. **Claude の調査だけを試す**: Gemini を飛ばすので費用を抑えられます (fixture の depth は quick)

   ```bash
   python -m src.main tests/fixtures/research_request.json --run-name try_claude --no-discord --fresh
   ```

3. **最初から最後まで通す**

   ```bash
   python -m src.main input_audio/specific.wav --fresh
   ```

参考として、2 を実行したときは 16 ターン、Web 取得 14 回、約 3.6 分、約 $0.67 で、出典 22 件・9 ページの PDF ができました (2026-10-12)。

---

## 13. 拡張ガイド

### 13.1 新しい入力経路を追加する (HTTP / M5Stack)

`Pipeline` の入口を呼ぶだけで追加できます。調査には数分以上かかるので、HTTP の応答は先に返してください。

```python
import threading
from src.config import load_settings
from src.errors import PipelineError
from src.pipeline import Pipeline

def start_research(wav_path: str, run_name: str) -> None:
    def job():
        try:
            Pipeline(load_settings(), run_name).run_from_audio(wav_path, source_type="m5stack")
        except PipelineError as e:
            print(e.describe())   # 必要なら Discord にエラーを通知する
    threading.Thread(target=job, daemon=True).start()
```

`app.py` の `/upload` で WAV を保存した直後に `start_research(file_path, f"audio_{timestamp}")` を呼べば、M5Stack からの入力がそのまま流れます。
同時に何件も受け付ける場合は、スレッドではなくキュー (例: `queue.Queue` と 1 本のワーカー) にしてください。調査が直列に処理されるので、レート制限やサブスク枠を守りやすくなります。

### 13.2 STT エンジンを差し替える

`SpeechTranscription` を返す関数を作り、`pipeline.py` の `transcribe` 呼び出しを差し替えます。
`segments` が取れないエンジンなら空配列で構いません。

### 13.3 Gemini 以外で依頼を構造化する

`SpeechTranscription` から `ResearchRequest` を返す関数を作ります。
`GeminiResearchDraft` と `draft_to_request()` はそのまま流用できます。

### 13.4 調査の質や観点を変える

- 調査方針や情報源の優先度は `research/prompts.py` の `SYSTEM_PROMPT` で変えます
- レポートに項目を追加するときは、次の 3 か所をそろえて変更します
  1. `schemas/report.py` の `ResearchReport` にフィールドを追加する (`description` を書くと LLM への指示になる)
  2. `pdf/generator.py` に描画処理を追加する
  3. `tests/fixtures/research_report.json` を更新する
- 調査を観点ごと (SNS / 公式 / 論文など) に並列化したい場合は、`ClaudeAgentOptions(agents={...})` でサブエージェントを定義します。サブエージェントを起動するツールを `tools` と `allowed_tools` に追加する必要があります

### 13.5 通知先を追加する (Slack、メールなど)

`src/notify/` に `send_pdf(...)` 相当のモジュールを追加し、`Pipeline.run_from_request()` の [5] で呼び分けます。

### 13.6 PDF の見た目を変える

`pdf/generator.py` の先頭にある定数 (`ACCENT` など) と、`_Writer` のメソッドを変更します。
調査をやり直さずに確認するには、`--no-discord` で再実行してください (10 章)。

---

## 14. 既存コード (app.py / features/) との関係

| | 既存 (`app.py` + `features/speech_to_text.py`) | 新規 (`src/`) |
|---|---|---|
| 役割 | M5Stack から WAV を受け取り、TXT に文字起こしする | 文字起こしから PDF の Discord 送信まで |
| 文字起こしの出力 | `texts/*.txt` (テキストのみ) | `transcription.json` (セグメントやメタ情報付き) |
| Whisper モデル | `features/` 内で別にキャッシュ | `src/stt/` 内でキャッシュ |
| Docker | `Dockerfile` / `docker-compose.yml` が対応 | 未対応 |

既存のコードは、仕様の方針に従って変更していません。M5Stack とつなぐ段階で、次のどちらかを選んでください。

- **A. app.py から `src` を呼ぶ** (13.1 節)。`features/speech_to_text.py` は不要になります
- **B. app.py は WAV の保存だけにする**。別のプロセスが `uploads/` を監視して `python -m src.main` を実行します

### Docker で動かす場合の追加作業

- `Dockerfile` に `COPY src ./src` を追加する
- 日本語フォントを入れる: `apt-get install -y fonts-noto-cjk`
- 環境変数を渡す (`env_file: .env`)
- `output/` と、Whisper のモデルキャッシュ (`~/.cache/huggingface`) をボリュームにする
- Claude の認証は `CLAUDE_CODE_OAUTH_TOKEN` か `ANTHROPIC_API_KEY` で渡す (8 章)

---

## 15. 既知の制約・今後の課題

- **WebSearch は Claude Code 組み込みのツールです。** X (旧 Twitter) は検索結果に出にくく、ログインが必要なページは WebFetch で読めません。SNS の情報は検索結果に出てくる範囲に限られます
- **調査の費用と時間は依頼によって変わります。** `depth=comprehensive` では数十ターンかかることがあります。`CLAUDE_MAX_TURNS`、`CLAUDE_MAX_BUDGET_USD`、`CLAUDE_TIMEOUT_SEC` で上限を設けてください
- **出典番号の整合性は警告だけです。** 存在しない番号を参照していても止めません (`_check_citations`)
- **PDF は文字単位で折り返します。** 長い英単語が途中で改行されることがあります。また、絵文字など、フォントに無い文字は表示されません
- **キャッシュは run_name 単位です。** 入力ファイルの中身が変わったかどうかは見ていません
- **並行実行は想定していません。** 同じ run_name で同時に実行すると、出力ファイルが競合します
- **Gemini のモデル名は廃止されることがあります。** 404 になったら `GEMINI_MODEL` を [公式のモデル一覧](https://ai.google.dev/gemini-api/docs/models) で確認してください
