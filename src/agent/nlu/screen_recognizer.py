"""Page/screen recognition from UI tree analysis."""

from collections import Counter
from typing import Optional

from agent.device.protocol import UIElement


def recognize_app(node: UIElement) -> Optional[str]:
    """Infer the running app package from the UI tree resource IDs."""
    rids: list[str] = []

    def _collect(n: UIElement) -> None:
        if n.resource_id:
            rids.append(n.resource_id)
        for c in n.children:
            _collect(c)

    _collect(node)
    if not rids:
        return None

    packages = [rid.split(":id/")[0] for rid in rids if ":id/" in rid]
    if packages:
        return Counter(packages).most_common(1)[0][0]
    return None


def recognize_page_type(node: UIElement) -> str:
    """Heuristically identify the current page type from UI tree text and resource IDs."""
    all_text: list[str] = []
    all_rid: list[str] = []

    def _collect(n: UIElement) -> None:
        if n.text:
            all_text.append(n.text)
        if n.resource_id:
            rid_part = n.resource_id.split("/")[-1] if "/" in n.resource_id else n.resource_id
            all_rid.append(rid_part)
        for c in n.children:
            _collect(c)

    _collect(node)
    joined_text = " ".join(all_text).lower()
    joined_rid = " ".join(all_rid).lower()

    if "search" in joined_rid or "search_box" in joined_rid:
        return "search"
    if any(w in joined_text for w in ("login", "sign in", "登录", "登入")):
        return "login"
    if any(w in joined_text for w in ("setting", "settings", "设置")):
        return "settings"
    if "recycler" in joined_rid or "list_view" in joined_rid:
        return "list"
    if "webview" in joined_rid:
        return "webview"
    if any(w in joined_text for w in ("checkout", "cart", "购物车", "结算", "支付")):
        return "checkout"
    if any(w in joined_rid for w in ("edit", "edittext")) and len([t for t in all_text if t]) > 5:
        return "form"

    return "unknown"
