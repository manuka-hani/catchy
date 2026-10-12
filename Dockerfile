FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/cache/huggingface \
    OUTPUT_DIR=/app/output

WORKDIR /app

# faster-whisper (libgomp / ffmpeg) と PDF 用の日本語フォント
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-ipafont-gothic \
        fonts-noto-cjk \
        git \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY features ./features
COPY src ./src

RUN mkdir -p uploads texts output

EXPOSE 5000

CMD ["python", "app.py"]
