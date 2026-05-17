"""Desktop GUI launcher — runs the web server internally and wraps the UI in a native window."""

import logging
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


def run_desktop(app, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start web server in background, open native desktop window."""
    import uvicorn
    from agent.ui.web_server import AgentWebServer

    web = AgentWebServer()
    web.config = app.config
    web.llm = app.llm
    web.bridge = app.bridge
    web.loop = app.loop
    web._setup_logging()

    # Wire WebSocket broadcast callbacks
    web.loop.on_action = lambda action, args: _schedule_broadcast(
        web, {"type": "action", "action": action, "args": args}
    )
    web.loop.on_speak = lambda text: _schedule_broadcast(
        web, {"type": "speak", "text": text}
    )

    # Start mDNS device discovery
    web._start_discovery()

    # Start uvicorn in a daemon thread
    server_url = f"http://{host}:{port}"

    def _run_server():
        uvicorn.run(web.app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

    # Wait for server to be ready
    _wait_for_server(server_url)

    # Open native window
    _open_window(server_url, title="Android Agent")


def _schedule_broadcast(web, data: dict) -> None:
    """Schedule a broadcast from a non-async context."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(web._broadcast(data))
    except RuntimeError:
        pass


def _wait_for_server(url: str, timeout: float = 10.0) -> None:
    """Poll until the server responds."""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    logger.warning("Server did not start within timeout, launching window anyway")


def _open_window(url: str, title: str = "Android Agent") -> None:
    """Open the native desktop window."""
    import webview

    window = webview.create_window(
        title=title,
        url=url,
        width=1100,
        height=750,
        min_size=(720, 480),
        resizable=True,
        fullscreen=False,
        confirm_close=False,
        background_color="#0d1117",
        text_select=True,
    )
    webview.start(gui="edgechromium" if sys.platform == "win32" else None)
