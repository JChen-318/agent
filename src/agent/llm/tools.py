"""Tool definitions for OpenAI function calling. All available phone actions."""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Tap at specific absolute coordinates on the screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate in pixels"},
                    "y": {"type": "integer", "description": "Y coordinate in pixels"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_by_text",
            "description": "Find a visible element containing the given text and click it. Prefer this over click() when you can identify the element by its visible label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text label of the element to click (exact or partial match).",
                    },
                    "instance": {
                        "type": "integer",
                        "description": "If multiple elements match, select which one (0 = first).",
                        "default": 0,
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": "Type text into the currently focused text field. Focus the field first (click into it) before typing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to type"},
                    "clear_first": {
                        "type": "boolean",
                        "description": "Clear existing text before typing",
                        "default": True,
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swipe",
            "description": "Perform a swipe/drag gesture from one point to another on the screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Start X coordinate"},
                    "y1": {"type": "integer", "description": "Start Y coordinate"},
                    "x2": {"type": "integer", "description": "End X coordinate"},
                    "y2": {"type": "integer", "description": "End Y coordinate"},
                    "duration_ms": {
                        "type": "integer",
                        "description": "Duration of the swipe in ms",
                        "default": 300,
                    },
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the current scrollable view in the specified direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "left", "right"],
                        "description": "Direction: 'forward' = scroll down, 'backward' = scroll up.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Number of scroll steps",
                        "default": 1,
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "long_press",
            "description": "Long-press at coordinates. Useful for context menus, drag-and-drop, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate"},
                    "y": {"type": "integer", "description": "Y coordinate"},
                    "duration_ms": {
                        "type": "integer",
                        "description": "Hold duration in ms",
                        "default": 1000,
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "back",
            "description": "Press the system back button.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "home",
            "description": "Go to the home screen.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_apps",
            "description": "Open the recent apps overview/multitasking view.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Launch an app by its Android package name. Common packages: com.tencent.mm (WeChat), com.ss.android.ugc.aweme (TikTok), com.tencent.mobileqq (QQ), com.taobao.taobao (Taobao), com.sina.weibo (Weibo), com.android.settings (Settings).",
            "parameters": {
                "type": "object",
                "properties": {
                    "package_name": {
                        "type": "string",
                        "description": "Android package name of the app to launch",
                    },
                },
                "required": ["package_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for a specified duration. Use after actions that need time (app launch, page load, animation).",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_ms": {
                        "type": "integer",
                        "description": "Wait duration in milliseconds",
                    },
                },
                "required": ["duration_ms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ui_tree",
            "description": "Refresh and get the current UI tree (shallow by default for performance). Call this after any action that changes the screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum depth to capture (default 6 for fast loading, use 3 for overview, 10+ for full detail).",
                        "default": 6,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_node",
            "description": "Lazy-load: expand a specific node at given coordinates to see its full subtree. Use when get_ui_tree truncated a node you need to inspect. Costs less than full get_ui_tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate of the node to expand"},
                    "y": {"type": "integer", "description": "Y coordinate of the node to expand"},
                    "max_depth": {
                        "type": "integer",
                        "description": "Max depth for expanded subtree (default 10).",
                        "default": 10,
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take a screenshot and return it as a base64-encoded image. Use when the UI tree does not capture visual content (images, charts, videos).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "Call when the user's request has been fully satisfied. Provide a brief summary of what was done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief description of what was accomplished.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a question when you need clarification or confirmation for a sensitive action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user.",
                    },
                },
                "required": ["question"],
            },
        },
    },
]
