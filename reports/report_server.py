"""报告服务器：GET 服务静态报告 + POST /save-review 把反馈写回服务器 reviews/。

用法:
    python3 reports/report_server.py <报告目录> [端口=8000]

通过 SSH 端口转发在本地浏览器访问 http://localhost:<端口>/。
浏览器点「导出反馈」→ fetch POST /save-review → 写到 <报告目录>/reviews/review_<时间>.md。
若报告是 scp 到本地打开（非本服务），POST 失败，前端自动回退为本地下载。
"""
import datetime
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPORT_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
REVIEWS = REPORT_DIR / "reviews"


class Handler(SimpleHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") != "/save-review":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        REVIEWS.mkdir(parents=True, exist_ok=True)
        name = "review_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".md"
        path = REVIEWS / name
        path.write_text(body, encoding="utf-8")
        payload = json.dumps({"ok": True, "path": str(path)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(payload)
        print(f"[saved] {path}")


def main():
    handler = partial(Handler, directory=str(REPORT_DIR))
    print(f"serving {REPORT_DIR} at http://127.0.0.1:{PORT}/  (reviews -> {REVIEWS})")
    ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
