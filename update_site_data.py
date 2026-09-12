import json
from pathlib import Path

from traffic_monitor_api import read_library_hours, read_official_traffic


def write_json(path: str, data: dict[str, object]) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


traffic = read_official_traffic()
library = read_library_hours()
if traffic.get("ok"):
    write_json("traffic.json", traffic)
if library.get("ok"):
    write_json("library-hours.json", library)
if not traffic.get("ok") and not library.get("ok"):
    raise RuntimeError("流量與圖書館資料都無法取得")