import os
import json
import base64
import threading
import mimetypes
import logging

logger = logging.getLogger(__name__)

class FileEncoder:
    def __init__(self, eval_js_cb):
        self._eval_js = eval_js_cb

    def encode_async(self, file_paths: list):
        if not file_paths: return
        for path in file_paths:
            threading.Thread(target=self._worker, args=(path,), daemon=True).start()

    def _worker(self, path: str):
        try:
            size = os.path.getsize(path)
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            raw_bytes = bytearray()
            
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    raw_bytes.extend(chunk)
                    self._eval_js(f"window.onFileEncodeProgress && window.onFileEncodeProgress({json.dumps({'path': path, 'progress': int((len(raw_bytes)/size)*100) if size else 100})})")
            
            payload = {
                "path": path, "filename": os.path.basename(path), "mime_type": mime,
                "b64_data": base64.b64encode(raw_bytes).decode("utf-8"),
                "size": size
            }
            self._eval_js(f"window.onFileEncodeComplete && window.onFileEncodeComplete({json.dumps(payload, ensure_ascii=False)})")
        except Exception as e:
            self._eval_js(f"window.onFileEncodeError && window.onFileEncodeError({json.dumps({'path': path, 'error': str(e)})})")