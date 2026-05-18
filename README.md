# Android Phone Control Agent

语音 + LLM 驱动的全自动 Android 手机操控 agent。Whisper 本地语音识别 → OpenAI 兼容 API 决策 → Android 无障碍服务执行。

## 架构

```
┌────────────────────────────────────────┐         ┌──────────────────────────────┐
│            PC / Windows                │  JSON   │        Android Phone          │
│                                        │◄─WS────►│                              │
│  ┌─────────┐  ┌────────┐  ┌─────────┐ │ Port    │  ┌─────────────────────────┐ │
│  │ Whisper │─►│  LLM   │─►│ Device  │ │ 18765   │  │ AccessibilityService    │ │
│  │ (语音)  │  │ (决策) │  │ Bridge  │ │         │  │ ├ WebSocket Server      │ │
│  └─────────┘  └────────┘  └─────────┘ │         │  │ ├ GestureExecutor       │ │
│                       │               │         │  │ └ UiTreeCapturer        │ │
│                 ┌─────┴─────┐         │         │  └─────────────────────────┘ │
│                 │ CLI / Web │         │         │                              │
│                 │  Desktop  │         │         └──────────────────────────────┘
│                 └───────────┘         │
└────────────────────────────────────────┘
```

## 项目结构

```
android-agent/
├── src/agent/
│   ├── main.py              # CLI 入口
│   ├── mainw.py             # Windows GUI 入口（无终端窗口）
│   ├── core/
│   │   ├── loop.py          # ReAct 主循环
│   │   ├── state.py         # 状态管理 + UI 树压缩
│   │   ├── planner.py       # 复杂任务分解
│   │   └── context.py       # LLM 上下文窗口管理
│   ├── llm/
│   │   ├── client.py        # OpenAI 兼容客户端（重试、降级、XML tool call 解析）
│   │   ├── tools.py         # 工具定义（function calling）
│   │   ├── prompts.py       # 系统提示词 + few-shot 示例
│   │   └── parser.py        # 响应解析
│   ├── device/
│   │   ├── bridge.py        # WebSocket 客户端（自动重连、重试、缓存）
│   │   ├── protocol.py      # 通信协议定义
│   │   ├── executor.py      # 高级动作编排
│   │   ├── adb.py           # ADB 管理（USB 连接、端口转发）
│   │   └── discovery.py     # mDNS/NSD 网络发现
│   ├── speech/
│   │   ├── transcriber.py   # faster-whisper 语音识别
│   │   ├── vad.py           # 语音活动检测
│   │   └── tts.py           # 文字转语音（可选）
│   ├── nlu/
│   │   ├── rule_engine.py   # 规则引擎（快速匹配简单指令）
│   │   ├── app_mapper.py    # 应用包名映射
│   │   ├── entity_extractor.py # 实体提取
│   │   ├── screen_recognizer.py # 屏幕识别
│   │   └── path_cache.py   # 已知 UI 路径缓存
│   ├── ui/
│   │   ├── cli.py           # 终端 UI
│   │   ├── desktop.py       # 桌面 GUI 启动器（pywebview）
│   │   ├── web_server.py    # Web 控制面板后端
│   │   ├── config.py        # 配置向导
│   │   └── static/index.html # Web 控制面板前端
│   └── config/
│       ├── config.py        # 配置加载（YAML + 环境变量）
│       └── default.yaml     # 默认配置
├── android-service/         # Android 无障碍服务 APK
│   └── app/src/main/kotlin/com/androidagent/
│       ├── service/
│       │   ├── AgentAccessibilityService.kt  # 无障碍服务主入口
│       │   ├── GestureExecutor.kt            # 手势分发
│       │   └── UiTreeCapturer.kt             # UI 树捕获
│       ├── server/
│       │   ├── WebSocketServer.kt            # WS 服务器（NanoHTTPD, 端口 18765）
│       │   └── CommandHandler.kt             # 命令分发
│       ├── capture/ScreenCapturer.kt         # 截图工具
│       ├── model/{Protocol,UiNode}.kt        # 数据模型
│       └── util/{Logger,PermissionHelper}.kt
├── .github/workflows/
│   ├── build-apk.yml        # CI 构建 Android APK
│   └── build-agent.yml      # CI 构建 Windows EXE
├── pyproject.toml
└── .env.example
```

## 快速开始

### 1. Android 端

安装 APK → 打开 **设置 → 无障碍 → Android Agent** → 开启服务。

手机端主界面会显示 WiFi IP 地址和端口。确保手机和电脑在同一网络。

或者通过 USB 连接（需要 ADB）：
```bash
adb forward tcp:18765 tcp:18765
```

### 2. PC 端

```bash
# 安装
cd android-agent
pip install -e ".[tts]"

# 配置
cp .env.example .env
# 编辑 .env 填入 API key、设备 IP 等

# 运行（终端模式）
android-agent --host 192.168.1.100

# 运行（持续对话模式 + 语音）
android-agent --host 192.168.1.100 --mode continuous --voice

# 运行（Web 控制面板）
android-agent --web

# 运行（桌面应用）
android-agent --desktop

# 单次命令
android-agent --host 192.168.1.100 --command "打开微信"
```

### 3. Windows 桌面版

从 [GitHub Actions](https://github.com/JChen-318/agent/actions) 下载 `AndroidAgent` artifact，解压后双击运行。

### 4. 配置说明

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | LLM API 地址 |
| `OPENAI_API_KEY` | - | API 密钥 |
| `LLM_MODEL` | `gpt-4o` | 模型名称 |
| `DEVICE_HOST` | `192.168.1.100` | 手机 IP |
| `DEVICE_PORT` | `18765` | WebSocket 端口 |
| `WHISPER_MODEL_SIZE` | `small` | Whisper 模型大小 (tiny/base/small/medium/large-v3) |
| `INTERACTION_MODE` | `single_command` | 交互模式 (single_command/continuous) |

## LLM 工具列表

Agent 通过 function calling 执行以下操作：

| 工具 | 说明 |
|------|------|
| `click(x, y)` | 点击坐标 |
| `click_by_text(text)` | 按文字查找并点击 |
| `type(text)` | 输入文字 |
| `swipe(x1,y1,x2,y2)` | 滑动 |
| `scroll(direction)` | 滚动 |
| `long_press(x, y)` | 长按 |
| `back()` | 返回 |
| `home()` | 主页 |
| `recent_apps()` | 最近应用 |
| `launch_app(pkg)` | 启动应用 |
| `get_ui_tree()` | 获取 UI 树 |
| `screenshot()` | 截图 (API 30+) |
| `wait(ms)` | 等待 |
| `task_complete()` | 任务完成 |
| `ask_user()` | 询问用户 |

## 工作流

```
语音输入 → Whisper 转写 → 规则引擎快速匹配
                              ↓ 无匹配
                         捕获 UI 树 → LLM 决策 → 批量执行工具调用 → 验证结果 → 循环
```

优化特性：
- **批量模式**：LLM 一次输出 3-5 个 tool call，减少往返
- **UI 树剪枝**：仅保留可操作节点，状态栏/导航栏自动跳过
- **决策缓存**：相同 UI 状态 + 同意图命中缓存
- **NLU 规则引擎**：简单指令不消耗 LLM token
- **ADB 自动检测**：USB 连接自动端口转发

## Android APK 构建

推送代码后 GitHub Actions 自动构建，或本地：

```bash
cd android-service
export ANDROID_HOME=/path/to/sdk
./gradlew assembleDebug
# APK 在 app/build/outputs/apk/debug/app-debug.apk
```

## 安全

- 三级确认：`none` / `sensitive` / `all`
- 敏感操作（输入、启动应用）可在 `sensitive` 级别触发确认
- 屏蔽密码、验证码、信用卡号等敏感输入
