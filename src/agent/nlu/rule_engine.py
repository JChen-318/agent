"""Rule engine — bypass LLM for common, predictable operations.

Design: two-layer matching
  Layer 1 — Intent rules (no UI needed): match user text → tool calls
  Layer 2 — Screen rules (UI needed): match current screen → tool calls

Each layer can return a complete action batch including task_complete.
"""

import hashlib
import logging
import re
import uuid
from typing import Optional

from agent.device.protocol import UIElement

logger = logging.getLogger(__name__)

# Short random ids for rule-generated tool calls
def _cid() -> str:
    return f"rule_{uuid.uuid4().hex[:6]}"


# ── Intent rules ──────────────────────────────────────────────────────────

def match_intent(user_input: str) -> Optional[list[dict]]:
    """Try to match user input to a known intent. Returns tool calls or None."""

    text = user_input.strip()

    # ── Open app ──────────────────────────────────────────────────────
    m = re.match(r"^(?:打开|启动|运行|open|launch)\s*['\"]?(.+?)['\"]?$", text, re.IGNORECASE)
    if m:
        app_name = m.group(1).strip().rstrip("。.")
        pkg = _resolve_package(app_name)
        if pkg:
            logger.info(f"Rule: open_app '{app_name}' -> {pkg}")
            return [
                {"_name": "launch_app", "id": _cid(), "package_name": pkg},
                {"_name": "task_complete", "id": _cid(), "summary": f"{app_name}已打开"},
            ]
        logger.info(f"Rule: open_app '{app_name}' -> unknown package, fall through to LLM")

    # ── Navigation ─────────────────────────────────────────────────────
    if re.match(r"^(?:返回|后退|back|go\s*back)$", text, re.IGNORECASE):
        logger.info("Rule: back")
        return [
            {"_name": "back", "id": _cid()},
            {"_name": "task_complete", "id": _cid(), "summary": "已返回"},
        ]

    if re.match(r"^(?:回(?:到|)桌面|主屏幕|home|go\s*(?:to\s*)?home)$", text, re.IGNORECASE):
        logger.info("Rule: home")
        return [
            {"_name": "home", "id": _cid()},
            {"_name": "task_complete", "id": _cid(), "summary": "已回到桌面"},
        ]

    if re.match(r"^(?:最近(?:应用|任务)|recent(?:\s*apps)?)$", text, re.IGNORECASE):
        logger.info("Rule: recent_apps")
        return [
            {"_name": "recent_apps", "id": _cid()},
            {"_name": "task_complete", "id": _cid(), "summary": "已打开最近任务"},
        ]

    # ── Scroll ─────────────────────────────────────────────────────────
    m = re.match(r"^(?:(?:往|向)([上下])|(?:scroll)\s*(up|down))(?:(?:滑|滚动|拉|拖)(?:动|)?)?$",
                 text, re.IGNORECASE)
    if m:
        direction_raw = (m.group(1) or m.group(2) or "").lower()
        if direction_raw in ("上", "up"):
            logger.info("Rule: scroll backward (up)")
            return [
                {"_name": "scroll", "id": _cid(), "direction": "backward", "steps": 1},
                {"_name": "task_complete", "id": _cid(), "summary": "已向上滚动"},
            ]
        else:
            logger.info("Rule: scroll forward (down)")
            return [
                {"_name": "scroll", "id": _cid(), "direction": "forward", "steps": 1},
                {"_name": "task_complete", "id": _cid(), "summary": "已向下滚动"},
            ]

    # ── Screenshot ─────────────────────────────────────────────────────
    if re.match(r"^(?:截图|截屏|screenshot|capture)$", text, re.IGNORECASE):
        logger.info("Rule: screenshot")
        return [
            {"_name": "screenshot", "id": _cid()},
            {"_name": "task_complete", "id": _cid(), "summary": "已截图"},
        ]

    # ── Wait / pause ───────────────────────────────────────────────────
    m = re.match(r"^(?:等待|wait|sleep)\s*(\d+)\s*(?:秒|s(?:ec(?:onds?)?)?)?$", text, re.IGNORECASE)
    if m:
        secs = int(m.group(1))
        ms = min(secs * 1000, 30000)
        logger.info(f"Rule: wait {ms}ms")
        return [
            {"_name": "wait", "id": _cid(), "duration_ms": ms},
            {"_name": "task_complete", "id": _cid(), "summary": f"已等待{secs}秒"},
        ]

    # ── Click by text ──────────────────────────────────────────────────
    m = re.match(r"^(?:点击|click|tap)\s*['\"]?(.+?)['\"]?$", text, re.IGNORECASE)
    if m:
        target = m.group(1).strip().rstrip("。.")
        logger.info(f"Rule: click_by_text '{target}'")
        return [
            {"_name": "click_by_text", "id": _cid(), "text": target},
            {"_name": "task_complete", "id": _cid(), "summary": f"已点击{target}"},
        ]

    # ── Type text ──────────────────────────────────────────────────────
    m = re.match(r"^(?:输入|type|enter|write)\s*['\"]?(.+?)['\"]?$", text, re.IGNORECASE)
    if m:
        content = m.group(1).strip().rstrip("。.")
        logger.info(f"Rule: type '{content}'")
        return [
            {"_name": "type", "id": _cid(), "text": content},
            {"_name": "task_complete", "id": _cid(), "summary": f"已输入{content}"},
        ]

    return None


# ── Screen rules ───────────────────────────────────────────────────────────

# Known system dialogs: (app_package_hint, element_texts) → action
SYSTEM_DIALOGS = [
    # Chinese system permission dialogs
    (None, ["允许"], "click_by_text", {"text": "允许"}),
    (None, ["确定"], "click_by_text", {"text": "确定"}),
    (None, ["取消"], "click_by_text", {"text": "取消"}),
    (None, ["始终允许"], "click_by_text", {"text": "始终允许"}),
    (None, ["仅在使用时允许"], "click_by_text", {"text": "仅在使用时允许"}),
    # English dialogs
    (None, ["Allow"], "click_by_text", {"text": "Allow"}),
    (None, ["OK"], "click_by_text", {"text": "OK"}),
    (None, ["Cancel"], "click_by_text", {"text": "Cancel"}),
    (None, ["Deny"], "click_by_text", {"text": "Deny"}),
    # Common popups
    (None, ["我知道了"], "click_by_text", {"text": "我知道了"}),
    (None, ["同意"], "click_by_text", {"text": "同意"}),
    (None, ["继续"], "click_by_text", {"text": "继续"}),
    (None, ["跳过"], "click_by_text", {"text": "跳过"}),
    (None, ["关闭"], "click_by_text", {"text": "关闭"}),
    (None, ["确认"], "click_by_text", {"text": "确认"}),
    (None, ["知道了"], "click_by_text", {"text": "知道了"}),
    (None, ["同意并继续"], "click_by_text", {"text": "同意并继续"}),
]


def match_screen(ui_tree: Optional[UIElement]) -> Optional[list[dict]]:
    """Match current screen against known patterns. Returns tool calls or None.

    Focus: system permission dialogs, common popups, predictable screens.
    These rules only return the NEXT action (not task_complete), because
    multiple screen-level rules may chain.
    """

    if not ui_tree:
        return None

    # Collect all visible text from the UI tree
    all_texts: list[str] = []

    def _collect(node: UIElement, depth: int = 0):
        if depth > 5:
            return
        if node.text:
            all_texts.append(node.text)
        if node.content_desc:
            all_texts.append(node.content_desc)
        for child in node.children:
            _collect(child, depth + 1)

    _collect(ui_tree)
    text_set = set(all_texts)

    # Check system dialogs
    for app_hint, required_texts, action, base_args in SYSTEM_DIALOGS:
        # All required texts must be present on screen
        if not all(t in text_set for t in required_texts):
            continue
        # If app_hint is set, check that we're in the right app
        if app_hint:
            from agent.nlu.screen_recognizer import recognize_app
            current_app = recognize_app(ui_tree)
            if current_app and app_hint not in current_app:
                continue

        # Found a match — return the action
        node = _find_first_text(ui_tree, required_texts[0])
        if node and node.is_clickable:
            center = node.get_center()
            if center:
                logger.info(f"Screen rule: click '{required_texts[0]}' at {center}")
                return [
                    {"_name": "click", "id": _cid(), "x": center[0], "y": center[1]},
                ]
        # Fallback: click_by_text
        logger.info(f"Screen rule: click_by_text '{required_texts[0]}' (dialog)")
        return [
            {"_name": "click_by_text", "id": _cid(), "text": required_texts[0]},
        ]

    # No screen match
    return None


def match_screen_dialog_sequence(ui_tree: Optional[UIElement]) -> Optional[list[dict]]:
    """Check if we're on a known screen that has a full resolution sequence.

    Unlike match_screen which returns single actions, this returns a complete
    batch including the dialog dismissal and the original intent retry.
    Used after a dialog is detected to auto-dismiss it.
    """

    if not ui_tree:
        return None

    all_texts: list[str] = []

    def _collect(node: UIElement, depth: int = 0):
        if depth > 5:
            return
        if node.text:
            all_texts.append(node.text)
        if node.content_desc:
            all_texts.append(node.content_desc)
        for child in node.children:
            _collect(child, depth + 1)

    _collect(ui_tree)
    text_set = set(all_texts)
    text_lower = {t.lower() for t in all_texts}

    # Android system permission dialog: "Allow" / "Deny"
    if any(kw in text_set for kw in ("允许", "Allow", "ALLOW")):
        dismiss_text = "允许"
        for t in ("允许", "Allow", "始终允许"):
            if t in text_set:
                dismiss_text = t
                break
        logger.info(f"Screen rule: dismiss permission dialog via '{dismiss_text}'")
        return [
            {"_name": "click_by_text", "id": _cid(), "text": dismiss_text},
            {"_name": "task_complete", "id": _cid(),
             "summary": "Permission dialog dismissed"},
        ]

    # "App has stopped" / crash dialog
    if "确定" in text_set and any(
        kw in text_lower for kw in ("已停止", "stopped", "has stopped", "isn't responding",
                                     "无响应", "崩溃", "crash")
    ):
        logger.info("Screen rule: dismiss crash dialog")
        return [
            {"_name": "click_by_text", "id": _cid(), "text": "确定"},
            {"_name": "task_complete", "id": _cid(),
             "summary": "Crash dialog dismissed"},
        ]

    # Update dialog (Google Play / system)
    if any(kw in text_set for kw in ("更新", "Update", "稍后", "Later")):
        logger.info("Screen rule: dismiss update dialog")
        dismiss = "稍后" if "稍后" in text_set else "Later"
        return [
            {"_name": "click_by_text", "id": _cid(), "text": dismiss},
            {"_name": "task_complete", "id": _cid(),
             "summary": "Update dialog dismissed"},
        ]

    return None


# ── Composite matching (intent + screen) ───────────────────────────────────

def match(user_input: str, ui_tree: Optional[UIElement] = None) -> Optional[list[dict]]:
    """Full rule match: intent first, then screen context.

    Returns complete tool call list (including task_complete), or None
    to fall through to LLM.
    """

    # Layer 1: Intent-only rules (no UI needed)
    result = match_intent(user_input)
    if result:
        return result

    # Layer 2: Screen-based rules (require UI)
    if ui_tree:
        result = match_screen_dialog_sequence(ui_tree)
        if result:
            return result

    return None


def get_next_action(ui_tree: Optional[UIElement]) -> Optional[dict]:
    """Get a single next action from screen rules. For use inside the ReAct loop
    when the LLM is not needed for the next obvious step."""
    if not ui_tree:
        return None

    result = match_screen(ui_tree)
    if result and result[0].get("_name") not in ("task_complete", "ask_user"):
        return result[0]

    return None


# ── Helpers ────────────────────────────────────────────────────────────────

def _resolve_package(name: str) -> Optional[str]:
    """Resolve app name to package name. Checks app_mapper first, then tries
    direct package name format."""
    from agent.nlu.app_mapper import resolve_package
    return resolve_package(name)


def _find_first_text(node: UIElement, text: str) -> Optional[UIElement]:
    """Find the first node with matching text."""
    if node.text == text:
        return node
    for child in node.children:
        found = _find_first_text(child, text)
        if found:
            return found
    return None
