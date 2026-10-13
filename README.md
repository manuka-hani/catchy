# catchy — キャッチアップ専用リサーチガジェット

M5Stack などのデバイスに話しかけるだけで、Claude がインターネットを調べ、その結果を PDF にまとめて Discord に届けるリサーチエージェントです。

これは会話 AI ではありません。気になったテーマを音声で投げると、自律的に調査してレポートを返します。

## どう動くのか

このリポジトリは、**`POST /upload` に生の音声データ (WAV など) が届くと、調査パイプラインが走る HTTP サーバー**です。

```text
M5Stack / curl など ──(POST /upload, 音声データ)──▶ サーバー (Docker)
                                                  │ すぐに 200 を返す
                                                  ▼ バックグラウンドで実行
                         faster-whisper で文字起こし
                                 ▼
                         Gemini で調査依頼として整理・構造化
                                 ▼
                         Claude (Agent SDK) が Web を多段階で調査
                                 ▼
                         日本語 PDF レポートを生成
                                 ▼
                         Discord Webhook に PDF を送信
```

- 調査には数分から数十分かかります。そのため、サーバーは音声を保存するとすぐに応答を返し、調査はバックグラウンドで続けます。
- 届いた依頼は 1 本のワーカーで 1 件ずつ順に処理します。続けて送ってもキューに積まれるだけです。
- 結果は Discord に届くので、送信側のデバイスは「受け付けました」と表示するだけで十分です。

## クイックスタート (Docker)

### 1. 用意するもの

- [Docker](https://docs.docker.com/get-docker/) と Docker Compose (v2)
- 次の 3 つの認証情報 (取得方法は[次の章](#2-環境変数を設定する))
  - Gemini API キー
  - Claude の認証情報 (`CLAUDE_CODE_OAUTH_TOKEN` または `ANTHROPIC_API_KEY`)
  - Discord Webhook URL

### 2. 環境変数を設定する

```bash
git clone <このリポジトリの URL>
cd catchy
cp .env.example .env
```

`.env` を開き、少なくとも次の 3 つを設定してください。

| 変数 | 必須 | 用途 | 取得方法 |
|---|---|---|---|
| `GEMINI_API_KEY` | ○ | 文字起こしの補正と、調査依頼の構造化 | [Google AI Studio](https://aistudio.google.com/apikey) で「API キーを作成」 |
| `CLAUDE_CODE_OAUTH_TOKEN` | ○ ※ | Claude による Web 調査 (Claude Pro / Max のサブスク枠を使う) | Claude Code をインストールした手元の PC で `claude setup-token` を実行し、表示されたトークンを貼る (有効期間 1 年) |
| `DISCORD_WEBHOOK_URL` | ○ | PDF レポートの送信先 | Discord のサーバー設定 →「連携サービス」→「ウェブフック」→「新しいウェブフック」→ チャンネルを選んで「ウェブフック URL をコピー」 |

※ Claude はサブスクの代わりに **API の従量課金**でも使えます。その場合は `CLAUDE_CODE_OAUTH_TOKEN` の代わりに、[Claude Console](https://console.anthropic.com/) で発行した `ANTHROPIC_API_KEY=sk-ant-...` を `.env` に書いてください (両方あると `ANTHROPIC_API_KEY` が優先されます)。
サブスク認証は個人で使う前提のものです。サーバーを他の人にも使わせる場合は API キー方式にしてください。

その他の項目は任意です。空欄なら既定値で動きます。

| 変数 | 既定値 | 説明 |
|---|---|---|
| `CLAUDE_MODEL` | 空 (Claude Code の既定) | 調査に使う Claude のモデル |
| `CLAUDE_MAX_TURNS` | `80` | 1 回の調査の最大ターン数 |
| `CLAUDE_MAX_BUDGET_USD` | 空 (無制限) | 1 回の調査の費用上限 (USD)。API キー方式なら設定を推奨します |
| `CLAUDE_TIMEOUT_SEC` | `2400` | 1 回の調査のタイムアウト (秒) |
| `GEMINI_MODEL` | `gemini-3.8-flash` | 使う Gemini のモデル |
| `GEMINI_TIMEOUT_SEC` | `120` | Gemini 1 リクエストのタイムアウト (秒) |
| `WHISPER_MODEL` | `small` | `tiny` / `base` / `small` / `medium` / `large-v3` など |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `cpu` / `int8` | GPU を使う場合は `cuda` / `float16` など |
| `PDF_FONT_PATH` / `PDF_BOLD_FONT_PATH` | 空 (自動検出) | 日本語フォントを明示する場合のパス |
| `OUTPUT_DIR` | `./output` | 出力先 (Docker では `/app/output` に固定) |

`.env` は `.gitignore` と `.dockerignore` に入っているため、Git にもイメージにも含まれません。**キーやトークンを Git にコミットしないでください。**

### 3. サーバーを起動する

```bash
docker compose up -d --build
```

ポート `5000` で待ち受けます。ログは次のコマンドで確認できます。

```bash
docker compose logs -f
```

初回の調査時に Whisper `small` モデル (約 500MB) をダウンロードします。モデルは Docker の名前付きボリューム (`huggingface`) にキャッシュされるので、2 回目以降はダウンロードしません。

止めるときは次のコマンドです。

```bash
docker compose down
```

`restart: always` を指定しているので、止めない限り PC の再起動後も自動で立ち上がります。

### 4. 音声を送って試す

```bash
curl -X POST http://localhost:5000/upload \
  -H "Content-Type: audio/wav" \
  --data-binary @sample.wav
```

成功すると次のような応答がすぐに返り、ログで調査の進行を追えます。数分から数十分後に Discord に PDF が届きます。

```json
{"status": "success", "message": "Saved as audio_20261011_224630.wav", "run_name": "audio_20261011_224630"}
```

## `/upload` API

| 項目 | 内容 |
|---|---|
| メソッド / パス | `POST /upload` |
| ボディ | 音声ファイルのバイト列をそのまま送る (multipart/form-data ではありません) |
| `Content-Type` | `audio/wav` を推奨 (`application/octet-stream` でも可) |
| 音声形式 | WAV を推奨します。中身は ffmpeg (PyAV) でデコードするので MP3 なども読めますが、保存時のファイル名は常に `.wav` になります |
| 言語 | 日本語 (`language="ja"`) で文字起こしします |

| ステータス | 意味 |
|---|---|
| `200` | 音声を保存し、調査をキューに積んだ。`run_name` は出力フォルダ名 |
| `400` | ボディが空 (`No data received`)。`curl -F` などの multipart 送信だとこうなります |
| `500` | 音声の保存に失敗した |

調査そのものの成否は HTTP 応答には含まれません。失敗した場合はサーバーのログに `ステップ '<step>' で失敗しました` と原因が出ます ([トラブルシューティング](#トラブルシューティング))。

### M5Stack から送る

M5Stack Core2 などで録音した WAV (ヘッダ付き) を、同じ LAN にあるサーバーの `http://<サーバーの IP>:5000/upload` に、上と同じ形式で POST してください。

```text
M5Stack Core2 ──(LAN, POST /upload, audio/wav)──▶ サーバー:5000 ──▶ 調査パイプライン
```

> [!WARNING]
> `/upload` には認証がありません。誰でも調査を起動でき、API の費用やサブスク枠を消費します。**インターネットに直接公開せず**、LAN 内か VPN 経由でのみ使ってください。

### 保存されるファイル

`docker-compose.yml` で次のフォルダをホストにマウントしています。

| ホスト側 | 内容 |
|---|---|
| `./uploads/` | 受け取った音声 (`audio_YYYYMMDD_HHMMSS.wav`) |
| `./output/<run_name>/json/` | 文字起こし・調査依頼・調査結果の JSON と、Claude の検索記録 |
| `./output/<run_name>/pdf/research_report.pdf` | Discord に送ったものと同じ PDF |

コンテナ内で CLI を直接使うこともできます。

```bash
docker compose run --rm audio-server python -m src.main /app/uploads/audio_20261011_224630.wav --source m5stack
```

## ローカル環境のセットアップ (Docker を使わない場合)

- Python 3.11 (3.10 以上)
- Windows / macOS / Linux
- 日本語 TrueType フォント (Windows・macOS は標準フォントを自動検出します。Linux は `apt install fonts-noto-cjk`)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # 値を編集する

python app.py          # http://localhost:5000/upload で待ち受ける
```

Claude Agent SDK には Claude Code CLI が同梱されているため、別途インストールする必要はありません。
ローカルでは、`claude` コマンドでログイン済みなら `CLAUDE_CODE_OAUTH_TOKEN` を設定しなくても、その認証情報が使われます。

## ローカルで CLI として実行する (開発者向け)

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

### 出力ファイル

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

#### 再実行

同じ `run_name` で再実行すると、既に存在する JSON を再利用します。

- `transcription.json` がある場合は Whisper を実行しません
- `research_request.json` がある場合は Gemini を呼びません
- `research_report.json` がある場合は Claude で再調査しません

PDF 生成と Discord 送信は毎回実行します。そのため、Discord 送信に失敗した場合も、同じコマンドを再実行するだけで送り直せます。中身が壊れた JSON は自動で作り直します。

### Discord への送信内容

PDF の添付と次のメッセージが届きます。

```text
調査完了
テーマ： AIエージェント
調査日時： 2026-10-11 22:46
レポート： research_report.pdf
要約： …
```

PDF の上限は 10MB です。レート制限 (429) とサーバーエラー (5xx) の場合は、自動で再試行します。

### テスト

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
| `curl` で `400 No data received` が返る | `-F` (multipart) ではなく `--data-binary @file.wav` で生データを送ってください |
| `docker compose up` でポート 5000 が使用中と出る | macOS では AirPlay レシーバーが 5000 番を使います。`docker-compose.yml` の `ports` を `"5050:5000"` などに変えてください |
| Docker で `Claude の認証に失敗しました` | コンテナ内には `claude` のログイン情報がありません。`.env` に `CLAUDE_CODE_OAUTH_TOKEN` か `ANTHROPIC_API_KEY` を設定し、`docker compose up -d` で再作成してください |
| `ステップ 'config' で失敗しました: GEMINI_API_KEY が設定されていません` | `.env` を作成してキーを設定してください |
| `ステップ 'stt' で失敗しました: 音声のデコード/文字起こしに失敗しました` | WAV が壊れていないか確認してください。M5Stack から送る場合は、WAV ヘッダが正しく付いているか確認してください |
| `Whisper モデル 'small' のロードに失敗しました` | 初回のモデルダウンロードにはネットワーク接続が必要です |
| `Gemini モデル '...' が見つかりません` | `GEMINI_MODEL` を[利用可能なモデル](https://ai.google.dev/gemini-api/docs/models)に変更してください |
| `ステップ 'research' で失敗しました: Claude の認証に失敗しました` | `CLAUDE_CODE_OAUTH_TOKEN` を設定するか、`claude` コマンドでログインしてください |
| `最大ターン数に達し…` | `CLAUDE_MAX_TURNS` を増やしてください |
| 調査がタイムアウトする | `CLAUDE_TIMEOUT_SEC` を増やすか、依頼の depth を下げてください |
| `日本語フォントが見つかりません` | `PDF_FONT_PATH` にフォントのパスを指定してください |
| `Discord Webhook が無効です (HTTP 404)` | Webhook が削除されていないか、URL が正しいか確認してください |

失敗したステップ名はログに `ステップ '<step>' で失敗しました` の形で出ます (Docker では `docker compose logs -f` で確認できます)。CLI の場合、終了コードは 1 です。詳細を見たい場合は CLI に `-v` を付けて実行してください。

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

内部設計・各ファイルの詳細・拡張方法は [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) を参照してください。
