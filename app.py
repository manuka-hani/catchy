import logging
import os
import queue
import threading
from datetime import datetime

from flask import Flask, jsonify, request

from src.config import load_settings, setup_logging
from src.errors import PipelineError
from src.pipeline import Pipeline

setup_logging()
logger = logging.getLogger("app")

app = Flask(__name__)

UPLOAD_FOLDER = "./uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 調査は時間がかかるので、リクエストを待たせず 1 本のワーカーで直列に処理する
_jobs: queue.Queue[tuple[str, str]] = queue.Queue()


def _research_worker() -> None:
    while True:
        file_path, run_name = _jobs.get()
        try:
            logger.info("調査を開始します: %s", run_name)
            Pipeline(load_settings(), run_name).run_from_audio(file_path, source_type="m5stack")
            logger.info("調査が完了しました: %s", run_name)
        except PipelineError as e:
            logger.error("%s", e.describe())
        except Exception:
            logger.exception("調査に失敗しました: %s", run_name)
        finally:
            _jobs.task_done()


threading.Thread(target=_research_worker, name="research-worker", daemon=True).start()


@app.route("/upload", methods=["POST"])
def upload_file():
    if not request.data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audio_{timestamp}.wav"
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        run_name = f"audio_{timestamp}"

        with open(file_path, "wb") as f:
            f.write(request.data)

        logger.info("Saved: %s (%d bytes)", file_path, len(request.data))
        _jobs.put((file_path, run_name))
        return jsonify({
            "status": "success",
            "message": f"Saved as {filename}",
            "run_name": run_name,
        }), 200

    except Exception as e:
        logger.exception("Error saving file")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    # 外部（M5Stackなど）からのアクセスを許可するため 0.0.0.0 で待機
    app.run(host="0.0.0.0", port=5000, threaded=True)
