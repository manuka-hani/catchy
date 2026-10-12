# catchy — キャッチアップ専用リサーチガジェット

M5Stack に話しかけるだけで、Claude がインターネットを調査し、その結果を PDF にまとめて Discord に届ける個人用リサーチエージェントです。

これは会話 AI ではありません。気になったテーマを音声で投げると、自律的に調査してレポートを返します。

内部設計・各ファイルの詳細・拡張方法は [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) を参照してください。

## アーキテクチャ

```text
M5Stack Core2 / CLI / HTTP          … 入力インターフェース (差し替え可能)
        │ WAV
        ▼
faster-whisper (small, CPU, int8)   src/stt/             → transcription.json
        │ SpeechTranscription
        ▼
Gemini Flash                        src/request_builder/ → research_request.json
        │ ResearchRequest (構造化された調査依頼)
        ▼
Claude Agent SDK                    src/research/        → research_report.json
  WebSearch / WebFetch で多段階調査      (+ research_trace.jsonl)
        │ ResearchReport (出典・事実/意見の分類つき)
        ▼
PDF 生成 (fpdf2 + 日本語フォント)    src/pdf/             → research_report.pdf
        ▼
Discord Webhook                     src/notify/
```

各ステップの入出力は Pydantic モデル (`src/schemas/`) で定義されており、ステップ同士はこの JSON だけでつながっています。

| モジュール | 責務 |
|---|---|
| `src/main.py` | CLI。入力ファイルの種類に応じてパイプラインの入口を選ぶ |
| `src/pipeline.py` | ステップの実行順序、途中結果の保存と再利用 |
| `src/stt/` | faster-whisper による文字起こし → `SpeechTranscription` |
| `src/request_builder/` | Gemini による誤認識補正・依頼の構造化 → `ResearchRequest` |
| `src/research/` | Claude Agent SDK による自律 Web 調査 → `ResearchReport` |
| `src/pdf/` | `ResearchReport` → 日本語 PDF |
| `src/notify/` | Discord Webhook への PDF 送信 |
| `src/schemas/` | 上記すべての JSON スキーマ (Pydantic) |
| `app.py` | M5Stack から WAV を受け取り、調査パイプラインをバックグラウンドで開始する |

### 役割分担

- **Gemini Flash** は調査をしません。STT の誤認識補正 (例: 「エジェント」→「エージェント」)、フィラーの除去、調査テーマ・期間・検索キーワード候補・比較対象の抽出だけを行い、JSON Schema に沿った出力を返します。
- **Claude Agent SDK** がシステムの中心です。使えるツールは `WebSearch` と `WebFetch` だけに制限しています。次の多段階手順で調査します。
  1. キーワードを日英で複数作る
  2. X / Reddit / Qiita / Zenn / HN / 公式 / GitHub / ニュース / 論文などを横断して検索する
  3. 見つけた情報から追加で検索する
  4. 複数の情報源を比較し、情報の新しさを確認する
  5. 主張を FACT / OPINION / USER_REPORT / INFERENCE に分類する

  結果は構造化出力 (`output_format: json_schema`) で受け取ります。

## 必要環境

- Python 3.11 (3.10 以上)
- Windows / macOS / Linux
- 日本語 TrueType フォント (Windows・macOS は標準フォントを自動検出します。Linux は `apt install fonts-noto-cjk`)
- 初回実行時は Whisper `small` モデル (約 500MB) を自動でダウンロードします

## セットアップ

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # 値を編集する
```

Claude Agent SDK には Claude Code CLI が同梱されているため、別途インストールする必要はありません。

### 必要な API キー / `.env` 設定

| 変数 | 必須 | 説明 |
|---|---|---|
| `GEMINI_API_KEY` | ○ | [Google AI Studio](https://aistudio.google.com/apikey) で発行します |
| `DISCORD_WEBHOOK_URL` | ○ | `--no-discord` を付けて実行する場合は不要です |
| `CLAUDE_CODE_OAUTH_TOKEN` | △ | 手元で `claude setup-token` を実行して得るトークンです。未設定の場合は、この PC でログイン済みの Claude Code (`claude` コマンド) の認証情報を使います |
| `GEMINI_MODEL` | | 既定値は `gemini-3.8-flash` |
| `CLAUDE_MODEL` | | 空の場合は Claude Code の既定モデルを使います |
| `CLAUDE_MAX_TURNS` / `CLAUDE_MAX_BUDGET_USD` / `CLAUDE_TIMEOUT_SEC` | | 調査量・コスト・時間の上限 |
| `WHISPER_MODEL` / `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | | 既定値は `small` / `cpu` / `int8` |
| `PDF_FONT_PATH` / `PDF_BOLD_FONT_PATH` | | 日本語フォントを明示的に指定する場合に使います |
| `OUTPUT_DIR` | | 既定値は `./output` |

`.env` は `.gitignore` に入っているため、Git にはコミットされません。

## Docker

```bash
cp .env.example .env   # 値を編集する
docker compose up --build
```

`POST /upload` で WAV を受け取ると、すぐ応答を返してからコンテナ内で調査パイプラインを実行します。結果の PDF は `./output/` と Discord に出ます。Whisper のモデルは名前付きボリュームにキャッシュします。

コンテナ内で CLI を使う場合:

```bash
docker compose run --rm audio-server python -m src.main /app/uploads/audio.wav --source m5stack
```

## 実行方法

```bash
# 音声ファイルから一気通貫で実行する (WAV → Whisper → Gemini → Claude → PDF → Discord)
python -m src.main ./input_audio/sample.wav

# Discord に送らずに確認する
python -m src.main ./input_audio/sample.wav --no-discord

# 音声認識をスキップして、テキストの依頼から実行する
python -m src.main ./tests/fixtures/specific.txt

# Gemini もスキップして、調査依頼 JSON から実行する
python -m src.main ./tests/fixtures/research_request.json

# 途中結果を使わずに最初からやり直す
python -m src.main ./input_audio/sample.wav --fresh
```

| オプション | 説明 |
|---|---|
| `--no-discord` | Discord に送信しません |
| `--fresh` | 保存済みの途中結果を使わず、全ステップを再実行します |
| `--run-name NAME` | 出力フォルダ名を指定します (既定は入力ファイル名) |
| `--source m5stack` | 入力元を `transcription.json` に記録します |
| `--output-dir DIR` | 出力先を指定します |
| `-v` | デバッグログを表示します (Claude CLI の stderr も含みます) |

調査には数分から数十分かかります。進行中は、実行中の検索クエリと取得中の URL がログに出ます。

## 出力ファイル

```text
output/<run_name>/
├── json/
│   ├── transcription.json      # Whisper が何を認識したか (segments: start/end/text など)
│   ├── research_request.json   # Gemini がどう解釈したか (最終的な調査依頼)
│   ├── research_report.json    # Claude の調査結果 (構造化レポート + 出典)
│   └── research_trace.jsonl    # Claude が実行した検索・取得の記録
├── pdf/
│   └── research_report.pdf
└── work/                       # Claude Agent SDK の作業ディレクトリ
```

### 再実行

同じ `run_name` で再実行すると、既に存在する JSON を再利用します。

- `transcription.json` がある場合は Whisper を実行しません
- `research_request.json` がある場合は Gemini を呼びません
- `research_report.json` がある場合は Claude で再調査しません

PDF 生成と Discord 送信は毎回実行します。そのため、Discord 送信に失敗した場合も、同じコマンドを再実行するだけで送り直せます。中身が壊れた JSON は自動で作り直します。

## Discord 設定

1. Discord のサーバー設定から「連携サービス」→「ウェブフック」→「新しいウェブフック」を開きます
2. 投稿先のチャンネルを選び、「ウェブフック URL をコピー」します
3. コピーした URL を `.env` の `DISCORD_WEBHOOK_URL` に設定します

送信内容は、PDF の添付と次のメッセージです。

```text
調査完了
テーマ： AIエージェント
調査日時： 2026-10-11 22:46
レポート： research_report.pdf
要約： …
```

PDF の上限は 10MB です。レート制限 (429) とサーバーエラー (5xx) の場合は、自動で再試行します。

## テスト

```bash
python -m pytest
```

外部 API はモックしてテストしています。テスト対象は次のとおりです。

- スキーマ検証
- Gemini 応答の検証とエラー処理 (不正 JSON、429 / 403 / 404、タイムアウト)
- 日本語 PDF の生成
- Discord 送信 (成功、無効な Webhook、レート制限、タイムアウト)
- パイプラインの再実行とスキップ
- CLI のエラー (存在しない WAV、API キーなし、Webhook なし)

`tests/fixtures/*.txt` には、通常、曖昧、具体的 (比較)、長めの依頼文が入っています。Windows では、音声合成 (Haruka) を使ってこれらを WAV にし、実際の STT を試せます。

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `ステップ 'config' で失敗しました: GEMINI_API_KEY が設定されていません` | `.env` を作成してキーを設定してください |
| `ステップ 'stt' で失敗しました: 音声のデコード/文字起こしに失敗しました` | WAV が壊れていないか確認してください。M5Stack から送る場合は、WAV ヘッダが正しく付いているか確認してください |
| `Whisper モデル 'small' のロードに失敗しました` | 初回のモデルダウンロードにはネットワーク接続が必要です |
| `Gemini モデル '...' が見つかりません` | `GEMINI_MODEL` を[利用可能なモデル](https://ai.google.dev/gemini-api/docs/models)に変更してください |
| `ステップ 'research' で失敗しました: Claude の認証に失敗しました` | `CLAUDE_CODE_OAUTH_TOKEN` を設定するか、`claude` コマンドでログインしてください |
| `最大ターン数に達し…` | `CLAUDE_MAX_TURNS` を増やしてください |
| 調査がタイムアウトする | `CLAUDE_TIMEOUT_SEC` を増やすか、依頼の depth を下げてください |
| `日本語フォントが見つかりません` | `PDF_FONT_PATH` にフォントのパスを指定してください |
| `Discord Webhook が無効です (HTTP 404)` | Webhook が削除されていないか、URL が正しいか確認してください |

失敗したステップ名はログに `ステップ '<step>' で失敗しました` の形で出ます。終了コードは 1 です。詳細を見たい場合は `-v` を付けて実行してください。

## M5Stack 接続

`app.py` の `POST /upload` が WAV を保存し、調査を 1 本のワーカーに積みます。HTTP の応答は保存直後に返します。結果は Discord と `output/<run_name>/` に出ます。

```text
M5Stack Core2 ──(LAN, POST /upload, audio/wav)──▶ app.py ──▶ Pipeline.run_from_audio(...)
```
