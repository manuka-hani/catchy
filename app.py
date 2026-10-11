import os
from datetime import datetime
from flask import Flask, request, jsonify

from features.speech_to_text import transcribe_and_save

app = Flask(__name__)

# 音声ファイルと文字起こし結果の保存先
UPLOAD_FOLDER = './uploads'
TEXT_FOLDER = './texts'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEXT_FOLDER, exist_ok=True)

@app.route('/upload', methods=['POST'])
def upload_file():
    # Content-Typeがaudio/wav（またはバイナリ系ヘッダー）か確認
    if not request.data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    try:
        # 日時をベースにしたファイル名を生成
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audio_{timestamp}.wav"
        file_path = os.path.join(UPLOAD_FOLDER, filename)

        # バイナリデータをそのままWAVファイルとして保存
        with open(file_path, 'wb') as f:
            f.write(request.data)

        print(f"Saved: {file_path} ({len(request.data)} bytes)")
        txt_filename = f"{timestamp}.txt"
        text = transcribe_and_save(file_path, TEXT_FOLDER, txt_filename)
        return jsonify({
            "status": "success",
            "message": f"Saved as {filename}",
            "text": text,
            "text_file": txt_filename,
        }), 200

    except Exception as e:
        print(f"Error saving file: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # 外部（M5Stackなど）からのアクセスを許可するため 0.0.0.0 で待機
    app.run(host='0.0.0.0', port=5000)
