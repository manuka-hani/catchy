FROM python:3.11-slim

WORKDIR /app

# 依存関係のインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードのコピー
COPY app.py .
COPY features ./features

# 保存先フォルダの作成
RUN mkdir uploads texts

# ポートの開放
EXPOSE 5000

# サーバーの起動
CMD ["python", "app.py"]
