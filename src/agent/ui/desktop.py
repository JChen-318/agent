"""Desktop GUI launcher — runs the web server internally and wraps the UI in a native window."""

import logging
import sys
import threading
import time
from pathlib import Path
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

    # Kill stale process on default port before looking for available
    from agent.ui.cli import _free_port
    _free_port(port)

    # Find an available port (handle stale process occupying default port)
    port = _find_available_port(host, port)

    # Register auto-connect on server startup
    @web.app.on_event("startup")
    async def _auto_connect():
        if web.bridge and not web._device_connected:
            try:
                await web.bridge.connect()
                web._device_connected = True
                logger.info(f"Bridge auto-connected to {web.bridge.host}:{web.bridge.port}")
            except Exception as e:
                logger.warning(f"Bridge auto-connect failed: {e}")

    # Start uvicorn in a daemon thread
    server_url = f"http://{host}:{port}"

    def _run_server():
        try:
            uvicorn.run(web.app, host=host, port=port, log_level="info")
        except Exception as e:
            logger.error(f"Uvicorn server crashed: {e}", exc_info=True)

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

    # Wait for server to be ready
    if not _wait_for_server(server_url, timeout=15.0):
        # If server still didn't start, try once more on a different port
        port = _find_available_port(host, port + 1, retry=3)
        server_url = f"http://{host}:{port}"
        logger.warning(f"Server startup delayed, switching to {server_url}")
        t2 = threading.Thread(target=_run_server, daemon=True)
        t2.start()
        _wait_for_server(server_url, timeout=10.0)

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


def _find_available_port(host: str, start_port: int, retry: int = 0) -> int:
    """Find an available port starting from start_port."""
    import socket
    port = start_port
    for _ in range(retry + 1):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:  # Port is free
                return port
        except Exception:
            pass
        port += 1
    return start_port  # Fallback to original


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    """Poll until the server responds. Returns True if server is ready."""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    logger.warning(f"Server did not start at {url} within {timeout}s")
    return False


def _open_window(url: str, title: str = "Android Agent") -> None:
    """Open the native desktop window."""
    import webview
    import traceback

    try:
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
    except Exception as e:
        # Write error to a log file next to the exe so user can diagnose
        import datetime
        log_path = Path(getattr(sys, '_MEIPASS', '.')) / "android-agent-error.log"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] WebView error: {e}\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:
            pass
        # Fallback: open in system browser
        import webbrowser
        webbrowser.open(url)
        print(f"Window creation failed: {e}. Opened in browser instead.", file=sys.stderr)
        # Keep server alive
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_forever()
        except KeyboardInterrupt:
            pass
