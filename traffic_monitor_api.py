"""Expose TrafficMonitor's daily traffic history to a local web page."""

from __future__ import annotations

import json
import re
import ssl
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

TRAFFIC_FILE = Path(r"C:\Users\user\TrafficMonitor_V1.86_x64\TrafficMonitor\history_traffic.dat")
OFFICIAL_IP = "140.125.207.114"
OFFICIAL_URL = f"https://netflow.yuntech.edu.tw/netflow.pl?action=ShowIP&IP={OFFICIAL_IP}"
HOST = "127.0.0.1"
PORT = 8765
LINE_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2})\s+(\d+)\/(\d+)$")
OFFICIAL_ROW_PATTERN = re.compile(
    r"(\d{4})\s+(\d{2})\s+(\d{2})\s+"
    r"\d+\.\d+\s+\d+\.\d+\s+\d+\.\d+\s+\d+\.\d+\s+"
    r"(\d+\.\d+)",
)
MAX_OFFICIAL_BYTES = 256 * 1024
OFFICIAL_CACHE_SECONDS = 60
LIBRARY_URL = "https://www.lib.yuntech.edu.tw/wSite/mp"
LIBRARY_CACHE_SECONDS = 600
LIBRARY_MAX_BYTES = 512 * 1024
_official_cache: tuple[float, dict[str, object]] | None = None
_official_cache_lock = threading.Lock()
_library_cache: tuple[float, dict[str, object]] | None = None
_library_cache_lock = threading.Lock()


def read_traffic() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    try:
        lines = TRAFFIC_FILE.read_text(encoding="utf-8").splitlines()
        for line in lines:
            match = LINE_PATTERN.match(line.strip())
            if match:
                rows.append(
                    {
                        "date": match.group(1),
                        "uploaded": int(match.group(2)),
                        "downloaded": int(match.group(3)),
                    }
                )
        modified_at = datetime.fromtimestamp(TRAFFIC_FILE.stat().st_mtime).astimezone()
    except (OSError, UnicodeError):
        return {"ok": False, "error": "找不到 TrafficMonitor 流量資料"}

    latest = rows[0] if rows else None
    if latest is None:
        return {"ok": False, "error": "TrafficMonitor 尚未產生流量資料"}
    return {
        "ok": True,
        "source": str(TRAFFIC_FILE),
        "updated_at": modified_at.isoformat(timespec="seconds"),
        "date": latest["date"],
        "uploaded_kb": latest["uploaded"],
        "downloaded_kb": latest["downloaded"],
        "total_kb": int(latest["uploaded"]) + int(latest["downloaded"]),
    }


def read_official_traffic() -> dict[str, object]:
    global _official_cache
    with _official_cache_lock:
        if _official_cache and time.monotonic() - _official_cache[0] < OFFICIAL_CACHE_SECONDS:
            return _official_cache[1].copy()

    try:
        # The campus system currently presents a certificate that Python cannot verify.
        context = ssl._create_unverified_context()
        with urlopen(OFFICIAL_URL, timeout=30, context=context) as response:
            text = ""
            bytes_read = 0
            match = None
            target_date = date.today().strftime("%Y %m %d")  # noqa: DTZ011
            while bytes_read < MAX_OFFICIAL_BYTES:
                chunk = response.read(8192)
                if not chunk:
                    break
                bytes_read += len(chunk)
                text += chunk.decode("utf-8", errors="replace")
                searchable_text = re.sub(r"<[^>]+>", " ", text)
                searchable_text = re.sub(r"\s+", " ", searchable_text)
                if target_date in searchable_text:
                    match = OFFICIAL_ROW_PATTERN.search(searchable_text)
                    if match and " ".join(match.group(1, 2, 3)) == target_date:
                        break
            else:
                match = None
    except OSError:
        with _official_cache_lock:
            if _official_cache:
                return _official_cache[1].copy()
        return {"ok": False, "error": "無法連線至雲科官方流量系統"}

    if match is None:
        with _official_cache_lock:
            if _official_cache:
                return _official_cache[1].copy()
        return {"ok": False, "error": "無法辨識官方流量資料"}
    result = {
        "ok": True,
        "source": "YunTech NetFlow",
        "ip": OFFICIAL_IP,
        "date": f"{match.group(1)}/{match.group(2)}/{match.group(3)}",
        "total_gb": float(match.group(4)),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with _official_cache_lock:
        _official_cache = (time.monotonic(), result)
    return result


def read_library_hours() -> dict[str, object]:
    global _library_cache
    with _library_cache_lock:
        if _library_cache and time.monotonic() - _library_cache[0] < LIBRARY_CACHE_SECONDS:
            return _library_cache[1].copy()

    try:
        context = ssl._create_unverified_context()
        with urlopen(LIBRARY_URL, timeout=20, context=context) as response:
            content = b""
            while len(content) < LIBRARY_MAX_BYTES:
                chunk = response.read(8192)
                if not chunk:
                    break
                content += chunk
                text = re.sub(r"<[^>]+>", " ", content.decode("utf-8", errors="replace"))
                text = re.sub(r"\s+", " ", text)
                match = re.search(r"本日(?:開館|閉館)\s*/?\s*(\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})", text)
                if match:
                    result = {
                        "ok": True,
                        "source": LIBRARY_URL,
                        "hours": match.group(1).replace(" ", ""),
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                    with _library_cache_lock:
                        _library_cache = (time.monotonic(), result)
                    return result
    except OSError:
        pass

    with _library_cache_lock:
        if _library_cache:
            return _library_cache[1].copy()
    return {"ok": False, "error": "無法取得圖書館今日開放時間"}


class TrafficHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/traffic", "/library"}:
            self.send_error(404)
            return
        data = read_official_traffic() if path == "/traffic" else read_library_hours()
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), TrafficHandler)
    print(f"TrafficMonitor API: http://{HOST}:{PORT}/traffic")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止 TrafficMonitor API。")
    finally:
        server.server_close()