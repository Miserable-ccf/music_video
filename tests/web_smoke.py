"""由 webapp-testing/with_server.py 启动服务后执行。"""

import json
import subprocess
import urllib.request


root = urllib.request.urlopen("http://127.0.0.1:8765/", timeout=5)
html = root.read().decode("utf-8")
assert root.status == 200 and "音樂影像製作台" in html

config = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/config", timeout=5))
assert config["offline_template"] and config["template_id"] == "black-gradient-v1"
assert "model" not in config
assert "api_key" not in json.dumps(config).lower()

subprocess.run([
    "/usr/bin/google-chrome", "--headless", "--no-sandbox", "--disable-gpu",
    "--window-size=1440,1000", "--screenshot=/tmp/music-video-web.png",
    "http://127.0.0.1:8765/",
], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
print("web smoke OK; screenshot=/tmp/music-video-web.png")
