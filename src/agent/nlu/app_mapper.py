"""App name to Android package name mapping with fuzzy matching."""

import re
from typing import Optional

# Primary lookup: natural language name -> package name
APP_MAP: dict[str, str] = {
    # Chinese apps
    "微信": "com.tencent.mm", "wechat": "com.tencent.mm",
    "抖音": "com.ss.android.ugc.aweme", "tiktok": "com.ss.android.ugc.aweme",
    "QQ": "com.tencent.mobileqq",
    "淘宝": "com.taobao.taobao", "taobao": "com.taobao.taobao",
    "微博": "com.sina.weibo", "weibo": "com.sina.weibo",
    "支付宝": "com.eg.android.AlipayGphone", "alipay": "com.eg.android.AlipayGphone",
    "美团": "com.sankuai.meituan",
    "饿了么": "me.ele",
    "京东": "com.jingdong.app.mall",
    "拼多多": "com.xunmeng.pinduoduo",
    "百度": "com.baidu.searchbox",
    "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap",
    "网易云音乐": "com.netease.cloudmusic",
    "QQ音乐": "com.tencent.qqmusic",
    "哔哩哔哩": "tv.danmaku.bili", "bilibili": "tv.danmaku.bili",
    "知乎": "com.zhihu.android",
    "小红书": "com.xingin.xhs",
    "今日头条": "com.ss.android.article.news",
    "钉钉": "com.alibaba.android.rimet",
    "腾讯会议": "com.tencent.wemeet.app",
    "闲鱼": "com.taobao.idlefish",
    "得物": "com.shizhuang.duapp",
    "快手": "com.smile.gifmaker",
    "酷狗音乐": "com.kugou.android",
    "滴滴出行": "com.sdu.didi.psnger",
    # System apps
    "日历": "com.android.calendar", "calendar": "com.android.calendar",
    "时钟": "com.android.deskclock", "clock": "com.android.deskclock",
    "相机": "com.android.camera", "camera": "com.android.camera",
    "通讯录": "com.android.contacts", "contacts": "com.android.contacts",
    "短信": "com.android.mms", "messages": "com.android.mms",
    "电话": "com.android.dialer", "phone": "com.android.dialer",
    "设置": "com.android.settings", "settings": "com.android.settings",
    "计算器": "com.android.calculator2",
    "文件管理": "com.android.fileexplorer",
    # International apps
    "youtube": "com.google.android.youtube",
    "gmail": "com.google.android.gm",
    "google maps": "com.google.android.apps.maps",
    "chrome": "com.android.chrome",
    "facebook": "com.facebook.katana",
    "instagram": "com.instagram.android",
    "whatsapp": "com.whatsapp",
    "telegram": "org.telegram.messenger",
    "spotify": "com.spotify.music",
    "netflix": "com.netflix.mediaclient",
    "amazon": "com.amazon.mShop.android.shopping",
    "reddit": "com.reddit.frontpage",
    "discord": "com.discord",
    "snapchat": "com.snapchat.android",
    "tinder": "com.tinder",
    "uber": "com.ubercab",
    "pinterest": "com.pinterest",
}

# Normalized alias lookup
_ALIASES: dict[str, str] = {}
for _name, _pkg in APP_MAP.items():
    _key = _name.lower().replace(" ", "").replace("-", "")
    _ALIASES[_key] = _pkg
    if "." in _pkg:
        _last = _pkg.split(".")[-1]
        _ALIASES[_last] = _pkg


def resolve_package(name: str) -> Optional[str]:
    """Resolve a natural language app name to a package name. Returns None if unresolved."""
    if name in APP_MAP:
        return APP_MAP[name]
    key = name.lower().replace(" ", "").replace("-", "")
    if key in _ALIASES:
        return _ALIASES[key]
    # Pass-through if it already looks like a package name
    if re.match(r"^[a-z][a-z0-9_.]*[a-z0-9]$", name):
        return name
    return None


def resolve_action_args(action: str, args: dict) -> dict:
    """Auto-resolve app package names in launch_app arguments."""
    if action == "launch_app" and "package_name" in args:
        pkg = args["package_name"]
        resolved = resolve_package(pkg)
        if resolved and resolved != pkg:
            args = dict(args)
            args["package_name"] = resolved
    return args
