"""GUI entry point — no console window. Always launches in desktop mode."""
import sys
import os
import datetime
import traceback

# Capture startup errors to a log file next to the exe
_LOG_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
_STARTUP_LOG = os.path.join(_LOG_DIR, "android-agent-startup.log")


def _log(msg: str) -> None:
    try:
        with open(_STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


try:
    _log("Desktop agent starting...")

    # When console=False, PyInstaller sets std* to None.
    # Redirect to os.devnull so libraries (uvicorn, etc.) don't crash on .isatty() or .write().
    _devnull = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = _devnull
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r")

    # Default to desktop mode when launched as GUI
    if "--desktop" not in sys.argv and "--web" not in sys.argv and "--command" not in sys.argv:
        sys.argv.append("--desktop")

    from agent.ui.cli import main

    if __name__ == "__main__":
        main()
    _log("Desktop agent exited normally")
except Exception as e:
    _log(f"FATAL ERROR: {e}\n{traceback.format_exc()}")
    raise
