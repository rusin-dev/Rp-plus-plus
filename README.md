# rp--your-programming-co-pilot

你的编程副驾驶（Your Programming Co-Pilot）

## 简介

这是一个基于 Python 的命令行 AI 编程助手。它通过系统提示词（Prompt）约束模型扮演"项目飞行员（Project Pilot）"的角色，将模糊的用户意图转化为清晰的执行蓝图，支持流式输出、交互式对话、终端内 Markdown 实时渲染（基于 rich）、多供应商切换、会话恢复与子 Agent 领域委派。

## 快速开始

```bash
# 克隆仓库
git clone git@gitee.com:mian-dev/rp--your-programming-co-pilot.git
cd rp--your-programming-co-pilot

# 创建并激活虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# （可选）以可执行命令方式安装
pip install -e .

# 配置 API 密钥
cp .env.example .env
# 编辑 .env，填入非供应商设置（日志等）
# 供应商配置使用 JSON：运行 rp 后输入 /connect 选择预设并输入 API Key
#   （详见下方「多供应商与模型」）
```

安装完成后，即可使用 `rp` 命令（等价于 `python -m src.main`）。

## 使用

```bash
# 单次提问
python -m src.main -m "帮我设计一个用户登录模块"

# 进入交互模式（输入 exit/quit/q 退出）
python -m src.main

# 指定其他提示词文件
python -m src.main -p SYSTEM_PROMPT.md -l general

# 以指定工作模式启动（plan / build / auto）
python -m src.main -M plan -m "帮我设计一个用户登录模块"

# 查看可用的提示词文件
python -m src.main --list-prompts
```

### 工作模式

| 模式 | 说明 |
| --- | --- |
| `plan` | 仅规划，不修改任何文件（防御性禁用 `shell` / `write` 工具） |
| `build` | 直接实现需求 |
| `auto` | 自动规划并实现（默认） |

- 交互模式中输入 `/mode` 查看/切换，或按 `Shift+Tab` 循环切换；
- 命令行可用 `-M/--mode <模式>` 指定启动模式。

### 子 Agent（领域委派）

Project Pilot 内置 5 个子 Agent，通过 `delegate` 工具自动委派领域专长任务：

| 子 Agent | 职责 |
| --- | --- |
| `librarian` | 知识检索与资料整理 |
| `frontend_builder` | 前端代码实现 |
| `backend_builder` | 后端代码实现 |
| `ui_ux_designer` | UI/UX 方案设计 |
| `reviewer` | 代码评审与质量把关 |

每个子 Agent 拥有独立提示词（`src/data/agents/`，frontmatter 声明角色描述与工具白名单）与独立的 LLM 调用循环，执行过程在终端实时展示，支持鼠标点击折叠。子 Agent 不向用户提问，也不再次委派。

### 斜杠命令（交互模式）

输入以 `/` 开头时，会自动弹出命令候选框：可用 `↑/↓` 或 `Tab` 切换候选，`Enter` 确认，`Esc` 关闭。也可直接输入完整命令回车执行。

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示所有可用命令 |
| `/variants` | 查看/切换思考强度（`fast` / `default` / `deep`） |
| `/models` | 列出当前供应商的可用模型；`/models <名称>` 切换 |
| `/connect` | 列出已配置的供应商；`/connect <名称>` 切换 |
| `/mode` | 查看/切换工作模式（`plan` / `build` / `auto`） |
| `/compact` | 压缩对话上下文（保留最近 20 条，可用 `/compact <n>` 指定） |
| `/usage` | 查看 token 用量与上下文窗口占用 |
| `/init` | 在工作区根目录生成 `AGENTS.md`（`/init -f` 覆盖已有文件） |
| `/session` | 列出已保存的会话；`/session <id>` 恢复指定会话继续对话 |
| `/clear` | 清空对话历史 |
| `/exit` / `/quit` | 退出 |

对话自动保存到 `.rp/sessions/`（已加入 `.gitignore`），下次启动可用 `/session` 恢复上下文。

### 多供应商与模型（JSON）

供应商配置使用 JSON 文件存储，不再使用环境变量：

- **预设模板**：`src/data/providers/preset/<名称>.json`，只含 `api_url` / `models` / `default_model`，不含 API Key，随项目分发。
- **使用预设**：运行 `rp` 后输入 `/connect`，底部固定区域会展示可用供应商列表，用 `↑ ↓` 切换、`Enter` 确认，随后提示输入 API Key，程序自动生成 `src/data/providers/<名称>.json`（预设元信息 + `api_key`）并切换。
- **当前选中**：`/connect`、`/models` 切换的 provider/model 会持久化到 `.rp/config.json`，下次启动自动恢复。

手动创建 `src/data/providers/<名称>.json` 示例：

```json
{
    "name": "deepseek",
    "api_url": "https://api.deepseek.com/v1",
    "models": ["deepseek-chat", "deepseek-reasoner"],
    "default_model": "deepseek-chat",
    "api_key": "sk-xxx"
}
```

其余设置仍在 `.env` 中配置：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `RP_VARIANT` | 思考强度（`fast` / `default` / `deep`） | `default` |
| `RP_MODE` | 工作模式（`plan` / `build` / `auto`） | `auto` |
| `SEARCH_BACKEND` | 网页搜索后端（`bing` / `ddg` / `auto`，`auto` 表示 ddg 失败时回退 bing） | `bing` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_DIR` | 日志目录 | `log/` |
| `SESSION_DIR` | 会话存储目录 | `.rp/sessions/` |
| `RICH_COLOR_SYSTEM` | 终端色彩系统（`auto` / `standard` / `256` / `truecolor` / `windows`） | `auto` |
| `RICH_THEME` | rich 主题 | 无 |
| `TAB_SIZE` | 制表符宽度 | `8` |

## 目录结构

项目采用三层架构：`core`（基础设施）→ `api`（能力）→ `ui`（表现）。

```
src/
├── main.py              # 入口：组装三层并启动
├── config.py            # 配置与校验（JSON 供应商预设 / 模式 / 变体）
├── core/                # 基础设施层
│   ├── logger.py        # 日志（文件 + 控制台）
│   ├── event_bus.py     # 事件总线（线程间通信）
│   ├── prompt.py        # 提示词加载
│   └── session.py       # 会话持久化（JSON 存储 / 加载 / 恢复）
├── api/                 # 能力层
│   ├── client.py        # OpenAI 客户端（后台线程 + 工具调用循环）
│   ├── agents.py        # 子 Agent 定义加载与独立运行循环
│   └── tools.py         # 工具定义（schema）与执行器
├── ui/                  # 表现层
│   ├── app.py           # rich TUI（Live 渲染 + 事件消费 + 会话恢复）
│   ├── input.py         # 输入框（斜杠命令补全 / 模式徽标 / 键位绑定）
│   ├── cancel_watcher.py# 后台监听连按两次 ESC，触发 CANCEL 事件以中断当前回答
│   ├── formatters.py    # 工具调用参数压缩为可读 name(参数) 展示文本
│   ├── mascot.py        # 启动吉祥物
│   └── subagent_panel.py# 子 Agent 执行面板（实时展示 / 折叠）
├── data/general/        # 系统提示词
├── data/agents/         # 子 Agent 提示词（frontmatter 声明角色与工具权限）
├── data/providers/preset/ # 供应商预设模板（JSON，不含 API Key）
└── data/providers/      # 使用预设后生成的供应商配置（含 API Key）
scripts/
├── build_exe.py         # Nuitka 一键编译单文件可执行程序
└── launcher.py          # 打包入口（转发到 src.main:main）
tests/                   # pytest 测试（core / api / ui / agents / session 等）
.github/workflows/       # GitHub Actions：CI / Format / Release / Snapshot / Auto Merge
pyproject.toml           # 项目元数据、ruff 与 pytest 配置、`rp` 命令入口
cost_map.json            # 主流模型价格参考（元 / 1M tokens，供 /usage 成本估算）
```

### 三层职责

| 层 | 职责 | 依赖 |
| --- | --- | --- |
| `core` | 日志、线程间通信（事件总线）、提示词加载、会话持久化 | 仅标准库 + config |
| `api` | OpenAI 请求、流式输出、工具定义与执行、子 Agent 运行 | core |
| `ui` | rich TUI：渲染消息、输入交互、子 Agent 面板、消费事件 | core + api |

内置工具：`ask`（向用户提问，经事件总线交互）、`read`（读工作区文件）、`write`（写工作区文件，写入内容以代码框预览）、`edit`（对已存在文件做精确替换，修改以 git 风格 diff 展示）、`grep`（正则搜索）、`shell`（执行命令）、`web_search`（网页搜索，默认 Bing，可用 `SEARCH_BACKEND` 切换）、`web_fetch`（抓取网页内容）、`delegate`（把领域专长任务委派给子 Agent）。工具调用参数在终端以可读形式展示，不再显示原始 JSON。读写工具默认锚定工作区根目录，防止越界访问。

通信模型：UI 主线程负责渲染与输入；API 请求在后台线程执行，通过 `EventBus` 发布 token / 工具调用 / 子 Agent 事件 / 错误等事件，UI 消费事件实时更新界面。工具 `ask` 依赖总线反向向 UI 提问并等待用户回答，形成完整闭环。

## 开发

```bash
pip install -r requirements-dev.txt
ruff check .            # 代码检查
ruff format .           # 代码格式化
pytest                  # 运行测试（CI 覆盖 Python 3.10 ~ 3.13）
```

## 打包发布

使用 [Nuitka](https://nuitka.net/) 将项目编译为单文件可执行程序（不跨平台，需在目标系统上分别构建）：

```bash
# 完整构建（产物在 dist/ 下：Windows -> rp.exe，Linux/macOS -> rp）
python scripts/build_exe.py

# 仅预览将要执行的 Nuitka 命令
python scripts/build_exe.py --dry-run
```

GitHub Actions 已内置 `Release` / `Snapshot` 工作流，可在打 tag 或定时任务时自动构建 Windows / Linux / macOS 三平台产物并发布。

## 贡献

欢迎提交 Issue 与 Pull Request，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
