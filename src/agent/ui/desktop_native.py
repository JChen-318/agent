"""Native desktop UI using tkinter — no browser, no HTML, no webview."""

import asyncio
import json
import logging
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import ttk, scrolledtext
from typing import Optional

logger = logging.getLogger(__name__)

# ── colors ──────────────────────────────────────────────────────────────
BG = "#1a1a2e"
FG = "#e0e0e0"
ACCENT = "#0f3460"
BTN_BG = "#16213e"
BTN_FG = "#e0e0e0"
ENTRY_BG = "#0f3460"
LOG_BG = "#0d1117"
GREEN = "#4caf50"
RED = "#f44336"
YELLOW = "#ff9800"
FONT = ("Consolas", 10)
FONT_BOLD = ("Consolas", 10, "bold")
FONT_TITLE = ("Consolas", 14, "bold")


class DesktopApp:
    """Native desktop window for Android Agent."""

    def __init__(self):
        self.app = None       # AgentApp
        self.bridge = None    # DeviceBridge
        self.config = None    # AppConfig
        self._agent_thread: Optional[threading.Thread] = None
        self._agent_loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._result_queue: queue.Queue = queue.Queue()
        self._command_lock = threading.Lock()
        self._device_connected = False
        self._discovery = None
        self._discovered_devices: list = []

    # ── startup ────────────────────────────────────────────────────────
    def run(self) -> None:
        """Start the desktop app (blocking — runs tkinter mainloop)."""
        self._init_agent()
        self._start_agent_thread()
        self._build_ui()
        self._start_periodic_refresh()
        self._auto_connect()
        self._root.mainloop()
        self._shutdown()

    def _init_agent(self) -> None:
        from agent.ui.cli import AgentApp
        self.app = AgentApp()
        self.app.init(skip_voice=True, skip_auto_usb=False)
        self.bridge = self.app.bridge
        self.config = self.app.config

    def _start_agent_thread(self) -> None:
        self._running = True

        def _run_agent():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._agent_loop = loop
            loop.run_forever()

        self._agent_thread = threading.Thread(target=_run_agent, daemon=True)
        self._agent_thread.start()

    def _run_async(self, coro, timeout: float = 30.0):
        """Run a coroutine in the agent thread and return result via queue."""
        if not self._agent_loop or not self._agent_loop.is_running():
            raise RuntimeError("Agent event loop not running")

        result_queue: queue.Queue = queue.Queue()

        async def _wrapper():
            try:
                r = await asyncio.wait_for(coro, timeout=timeout)
                result_queue.put(("ok", r))
            except Exception as e:
                result_queue.put(("error", e))

        self._agent_loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_wrapper())
        )

        kind, value = result_queue.get(timeout=timeout + 5)
        if kind == "error":
            raise value
        return value

    # ── UI construction ─────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self._root = tk.Tk()
        self._root.title("Android Agent")
        self._root.geometry("960x700")
        self._root.minsize(720, 480)
        self._root.configure(bg=BG)

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=FONT)
        style.configure("TButton", background=BTN_BG, foreground=BTN_FG,
                        font=FONT_BOLD, borderwidth=0, padding=4)
        style.map("TButton", background=[("active", ACCENT)])

        # ── Title bar ──
        title_frame = tk.Frame(self._root, bg=BG, pady=6)
        title_frame.pack(fill=tk.X)
        tk.Label(title_frame, text="Android Agent", font=FONT_TITLE,
                 bg=BG, fg=GREEN).pack(side=tk.LEFT, padx=10)

        # ── Status line ──
        self._status_var = tk.StringVar(value="Initializing...")
        self._status_label = tk.Label(self._root, textvariable=self._status_var,
                                       font=FONT, bg=BG, fg=YELLOW)
        self._status_label.pack(fill=tk.X, padx=10, pady=2)

        # ── Device info ──
        info_frame = tk.Frame(self._root, bg=BG)
        info_frame.pack(fill=tk.X, padx=10, pady=2)
        self._device_var = tk.StringVar(value="Device: --")
        self._model_var = tk.StringVar(value="")
        tk.Label(info_frame, textvariable=self._device_var, font=FONT,
                 bg=BG, fg=FG).pack(side=tk.LEFT)
        tk.Label(info_frame, textvariable=self._model_var, font=FONT,
                 bg=BG, fg="#888").pack(side=tk.LEFT, padx=(10, 0))

        # ── Device list ──
        dev_frame = tk.LabelFrame(self._root, text="Devices", font=FONT_BOLD,
                                   bg=BG, fg=FG, padx=5, pady=5)
        dev_frame.pack(fill=tk.X, padx=10, pady=4)
        self._dev_listbox = tk.Listbox(dev_frame, height=3, font=FONT,
                                        bg=LOG_BG, fg=FG, selectbackground=ACCENT)
        self._dev_listbox.pack(fill=tk.X)
        self._dev_listbox.bind("<<ListboxSelect>>", self._on_device_select)

        dev_btn_frame = tk.Frame(dev_frame, bg=BG)
        dev_btn_frame.pack(fill=tk.X, pady=(4, 0))
        tk.Button(dev_btn_frame, text="Connect", command=self._connect_selected,
                  bg=GREEN, fg="white", font=FONT_BOLD, padx=10).pack(side=tk.LEFT, padx=2)
        tk.Button(dev_btn_frame, text="Disconnect", command=self._disconnect,
                  bg=RED, fg="white", font=FONT_BOLD, padx=10).pack(side=tk.LEFT, padx=2)
        tk.Button(dev_btn_frame, text="Scan", command=self._scan_devices,
                  bg=BTN_BG, fg=BTN_FG, font=FONT, padx=8).pack(side=tk.LEFT, padx=2)
        tk.Button(dev_btn_frame, text="Refresh", command=self._refresh_devices,
                  bg=BTN_BG, fg=BTN_FG, font=FONT, padx=8).pack(side=tk.LEFT, padx=2)

        # ── Quick actions ──
        actions_frame = tk.LabelFrame(self._root, text="Quick Actions", font=FONT_BOLD,
                                       bg=BG, fg=FG, padx=5, pady=5)
        actions_frame.pack(fill=tk.X, padx=10, pady=4)
        actions = [
            ("Home", self._do_home), ("Back", self._do_back),
            ("Recents", self._do_recents), ("Screenshot", self._do_screenshot),
            ("UI Tree", self._do_ui_tree), ("Scroll ▲", self._do_scroll_up),
            ("Scroll ▼", self._do_scroll_down), ("Ping", self._do_ping),
        ]
        for label, cmd in actions:
            tk.Button(actions_frame, text=label, command=cmd,
                      bg=BTN_BG, fg=BTN_FG, font=FONT, padx=8,
                      relief=tk.RAISED).pack(side=tk.LEFT, padx=2, pady=2)

        # ── Command input ──
        cmd_frame = tk.Frame(self._root, bg=BG)
        cmd_frame.pack(fill=tk.X, padx=10, pady=(6, 2))
        tk.Label(cmd_frame, text="Command:", font=FONT_BOLD, bg=BG, fg=FG
                 ).pack(side=tk.LEFT)
        self._cmd_entry = tk.Entry(cmd_frame, font=("Consolas", 11),
                                    bg=ENTRY_BG, fg=FG, insertbackground=FG)
        self._cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        self._cmd_entry.bind("<Return>", lambda e: self._do_command())
        tk.Button(cmd_frame, text="Execute", command=self._do_command,
                  bg=ACCENT, fg="white", font=FONT_BOLD, padx=12
                  ).pack(side=tk.LEFT)

        # ── Output log ──
        log_frame = tk.LabelFrame(self._root, text="Output", font=FONT_BOLD,
                                   bg=BG, fg=FG, padx=5, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self._log = scrolledtext.ScrolledText(
            log_frame, font=FONT, bg=LOG_BG, fg=FG, insertbackground=FG,
            wrap=tk.WORD, state=tk.DISABLED,
        )
        self._log.pack(fill=tk.BOTH, expand=True)
        self._log.tag_configure("ok", foreground=GREEN)
        self._log.tag_configure("err", foreground=RED)
        self._log.tag_configure("info", foreground="#888")
        self._log.tag_configure("action", foreground=YELLOW)

        # ── Bottom bar ──
        bottom = tk.Frame(self._root, bg=BG, pady=2)
        bottom.pack(fill=tk.X, padx=10)
        self._agent_var = tk.StringVar(value="Agent: initializing...")
        tk.Label(bottom, textvariable=self._agent_var, font=FONT,
                 bg=BG, fg="#888").pack(side=tk.LEFT)

        self._log_message("Android Agent ready.", "ok")

    # ── logging ─────────────────────────────────────────────────────────
    def _log_message(self, msg: str, tag: str = "info") -> None:
        def _write():
            self._log.configure(state=tk.NORMAL)
            self._log.insert(tk.END, f"  {msg}\n", tag)
            self._log.see(tk.END)
            self._log.configure(state=tk.DISABLED)
        self._root.after(0, _write)

    # ── device connection ───────────────────────────────────────────────
    def _auto_connect(self) -> None:
        def _connect():
            try:
                self._run_async(self.bridge.connect())
                self._device_connected = True
                self._update_status("Online", GREEN)
                info = f"{self.bridge.host}:{self.bridge.port}"
                self._root.after(0, lambda: self._device_var.set(f"Device: {info}"))
                self._log_message(f"Connected to {info}", "ok")
            except Exception as e:
                self._log_message(f"Auto-connect failed: {e}", "err")
                self._update_status("Offline — click Connect", RED)
        threading.Thread(target=_connect, daemon=True).start()

    def _connect_selected(self) -> None:
        sel = self._dev_listbox.curselection()
        if not sel:
            self._log_message("Select a device first", "info")
            return
        idx = sel[0]
        if idx < len(self._discovered_devices):
            dev = self._discovered_devices[idx]
            host = dev.get("address") or dev.get("serial", "")
            port = dev.get("port", 18765)
            self._log_message(f"Connecting to {host}:{port}...", "info")

            def _connect():
                try:
                    self.bridge.host = host
                    self.bridge.port = port
                    self._run_async(self.bridge.connect())
                    self._device_connected = True
                    self._update_status("Online", GREEN)
                    self._root.after(0, lambda: self._device_var.set(
                        f"Device: {host}:{port}"))
                    self._log_message(f"Connected to {host}:{port}", "ok")
                except Exception as e:
                    self._log_message(f"Connection failed: {e}", "err")
            threading.Thread(target=_connect, daemon=True).start()

    def _disconnect(self) -> None:
        def _do():
            try:
                self._run_async(self.bridge.disconnect())
                self._device_connected = False
                self._update_status("Offline", RED)
                self._log_message("Disconnected", "info")
            except Exception as e:
                self._log_message(f"Disconnect error: {e}", "err")
        threading.Thread(target=_do, daemon=True).start()

    # ── command execution ───────────────────────────────────────────────
    def _do_command(self) -> None:
        text = self._cmd_entry.get().strip()
        if not text:
            return
        self._cmd_entry.delete(0, tk.END)
        self._log_message(f"> {text}", "action")

        if self._command_lock.locked():
            self._log_message("  (busy, please wait...)", "info")
            return

        def _execute():
            with self._command_lock:
                try:
                    # Try rule engine first (fast path, no LLM)
                    from agent.nlu.rule_engine import match
                    tree = None
                    try:
                        tree_data = self._run_async(self.bridge.get_ui_tree(max_depth=5))
                        from agent.device.protocol import UIElement
                        tree = UIElement.from_dict(
                            tree_data.get("ui_tree", tree_data)
                        )
                    except Exception:
                        pass

                    rule_result = match(text, tree)
                    if rule_result and not any(
                        c.get("_name") == "task_complete" and "needs LLM" in str(c)
                        for c in rule_result
                    ):
                        self._log_message(f"  [Rule engine matched]", "info")
                        for tool_call in rule_result:
                            if tool_call.get("_name") == "task_complete":
                                summary = tool_call.get("summary", "done")
                                self._log_message(f"  Done: {summary}", "ok")
                                break
                            action = tool_call.get("_name", "")
                            args = {k: v for k, v in tool_call.items()
                                    if k not in ("_name", "id")}
                            self._log_message(f"  Action: {action}({args})", "action")
                            self._run_async(
                                self.bridge.execute(action, args, timeout=30.0)
                            )
                        return

                    # Use LLM
                    from agent.llm.tools import TOOL_DEFINITIONS
                    from agent.llm.prompts import SYSTEM_PROMPT

                    llm = self.app.llm
                    resp = self._run_async(llm.chat(
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": text},
                        ],
                        tools=TOOL_DEFINITIONS,
                        tool_choice="auto",
                    ))

                    if resp.tool_calls:
                        for tc in resp.tool_calls:
                            if tc.name == "task_complete":
                                self._log_message(
                                    f"  Done: {tc.arguments.get('summary', 'done')}", "ok"
                                )
                                break
                            self._log_message(
                                f"  Action: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})",
                                "action"
                            )
                            self._run_async(
                                self.bridge.execute(tc.name, tc.arguments, timeout=30.0)
                            )
                    elif resp.content:
                        self._log_message(f"  {resp.content}", "info")
                except Exception as e:
                    self._log_message(f"  Error: {e}", "err")
                    traceback.print_exc()

        threading.Thread(target=_execute, daemon=True).start()

    # ── quick actions ───────────────────────────────────────────────────
    def _do_home(self):
        threading.Thread(target=self._simple_action, args=("home", {}), daemon=True).start()

    def _do_back(self):
        threading.Thread(target=self._simple_action, args=("back", {}), daemon=True).start()

    def _do_recents(self):
        threading.Thread(target=self._simple_action, args=("recent_apps", {}), daemon=True).start()

    def _do_screenshot(self):
        def _ss():
            try:
                img = self._run_async(self.bridge.screenshot())
                if img:
                    import base64
                    data = base64.b64decode(img)
                    self._log_message(f"Screenshot: {len(data)} bytes (PNG)", "ok")
                else:
                    self._log_message("Screenshot failed", "err")
            except Exception as e:
                self._log_message(f"Screenshot error: {e}", "err")
        threading.Thread(target=_ss, daemon=True).start()

    def _do_ui_tree(self):
        def _tree():
            try:
                tree = self._run_async(self.bridge.get_ui_tree(max_depth=5))
                self._log_message(f"UI tree: {len(str(tree))} chars (depth=5)", "ok")
            except Exception as e:
                self._log_message(f"UI tree error: {e}", "err")
        threading.Thread(target=_tree, daemon=True).start()

    def _do_scroll_up(self):
        threading.Thread(target=self._simple_action, args=("scroll", {"direction": "backward"}), daemon=True).start()

    def _do_scroll_down(self):
        threading.Thread(target=self._simple_action, args=("scroll", {"direction": "forward"}), daemon=True).start()

    def _do_ping(self):
        def _p():
            try:
                ok = self._run_async(self.bridge.ping())
                self._log_message(f"Ping: {'OK' if ok else 'FAIL'}", "ok" if ok else "err")
            except Exception as e:
                self._log_message(f"Ping error: {e}", "err")
        threading.Thread(target=_p, daemon=True).start()

    def _simple_action(self, action: str, args: dict) -> None:
        try:
            r = self._run_async(self.bridge.execute(action, args, timeout=10.0))
            status = r.get("status", "?")
            self._log_message(
                f"{action}: {status}", "ok" if status == "ok" else "err"
            )
        except Exception as e:
            self._log_message(f"{action} error: {e}", "err")

    # ── discovery ───────────────────────────────────────────────────────
    def _scan_devices(self) -> None:
        from agent.device.discovery import DeviceDiscovery
        self._log_message("Scanning for devices...", "info")

        def _scan():
            # Quick ADB check
            from agent.device.adb import AdbManager
            adb = AdbManager()
            if adb.available:
                devices = adb.list_devices()
                for d in devices:
                    info = adb.get_device_info(d.serial)
                    self._discovered_devices.append({
                        "name": info.get("model", d.serial),
                        "serial": d.serial,
                        "address": d.serial,
                        "type": "usb",
                        "model": info.get("model", ""),
                        "android": info.get("android_version", ""),
                        "port": 18765,
                    })
            self._root.after(0, self._refresh_devices)
            self._log_message(f"Scan complete: {len(self._discovered_devices)} device(s)", "ok")

        threading.Thread(target=_scan, daemon=True).start()

    def _refresh_devices(self) -> None:
        self._dev_listbox.delete(0, tk.END)
        for d in self._discovered_devices:
            label = f"{d.get('name','?')} [{d.get('address','?')}]"
            if d.get("android"):
                label += f"  Android {d['android']}"
            self._dev_listbox.insert(tk.END, label)

    def _on_device_select(self, event) -> None:
        sel = self._dev_listbox.curselection()
        if sel and sel[0] < len(self._discovered_devices):
            d = self._discovered_devices[sel[0]]
            self._root.after(0, lambda: self._device_var.set(
                f"Selected: {d.get('name','?')} — {d.get('address','?')}"
            ))

    # ── periodic refresh ────────────────────────────────────────────────
    def _start_periodic_refresh(self) -> None:
        def _refresh():
            if not self._running:
                return
            # Update agent info
            if self.app and self.app.loop:
                mode = self.config.interaction.mode if self.config else "?"
                model = self.config.llm.model if self.config else "?"
                self._agent_var.set(
                    f"Model: {model} | Mode: {mode}"
                )
            self._root.after(5000, _refresh)
        self._root.after(2000, _refresh)

    def _update_status(self, text: str, color: str) -> None:
        def _do():
            self._status_var.set(f"Status: {text}")
            self._status_label.configure(fg=color)
        self._root.after(0, _do)

    # ── shutdown ────────────────────────────────────────────────────────
    def _shutdown(self) -> None:
        self._running = False
        if self.bridge:
            try:
                self._run_async(self.bridge.disconnect())
            except Exception:
                pass
        if self._agent_loop and self._agent_loop.is_running():
            self._agent_loop.call_soon_threadsafe(self._agent_loop.stop)
