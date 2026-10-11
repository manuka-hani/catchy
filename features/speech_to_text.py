import os
from datetime import datetime
from faster_whisper import WhisperModel

_model = None

def get_model():
    global _model
    if _model is None:
        # 初回だけ読み込む。モデルが無ければ自動ダウンロードされる
        _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model

def transcribe_and_save(wav_path, output_dir, txt_filename):
    # 1. 保存先フォルダがなければ作成
    os.makedirs(output_dir, exist_ok=True)

    model = get_model()

    print("文字起こしを開始します...")
    # 2. 音声ファイルのテキスト化 (日本語を指定)
    segments, info = model.transcribe(wav_path, language="ja")

    # 3. テキストデータの結合
    full_text = ""
    for segment in segments:
        full_text += segment.text + "\n"

    # 4. 指定したローカルフォルダに、日付のファイル名で保存
    output_path = os.path.join(output_dir, txt_filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    print(f"保存が完了しました: {output_path}")
    return full_text

# --- 実行部分 ---
if __name__ == "__main__":
    # あなたのWAVファイルのパス
    target_wav = "./input_audio/sample.wav"
    # 保存したいローカルフォルダのパス
    output_folder = "./texts"
    txt_filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"

    transcribe_and_save(target_wav, output_folder, txt_filename)