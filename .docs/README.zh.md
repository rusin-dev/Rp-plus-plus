# rp++

<div align="center">
<img src="https://github.com/rusin-dev/Rp-plus-plus/blob/master/image.png">
</div>

## 简介

这是一个基于 Python 的命令行 AI 编程助手。它通过系统提示词（Prompt）约束模型扮演 Project Pilot（资深项目工程师）的角色，将模糊的用户意图转化为清晰的执行蓝图，支持流式输出、交互式对话、终端内 Markdown 实时渲染、多供应商切换、会话恢复与子 Agent 领域委派。

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
| `plan` | 仅规划，不修改任何文件（防御性禁用 `shell` / `write` / `edit` 工具） |
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
| `/variants` | 查看/切换思考强度（`low` / `medium` / `high` / `max`，以 `reasoning_effort` 传入 API） |
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

### 底部状态栏与待办清单

输入框下方有底部状态栏，右侧显示当前模式与模型。当模型通过 `create_todo_list` 创建待办清单后，清单也会显示在底部状态栏中，并用横线与输入框分隔：

```
────────────────────────────────────────────────────────────
  1. [ ] 分析需求
  2. [~] 设计接口
  3. [x] 编写测试
⏸ auto mode on · /help 查看快捷键    deepseek-v4-flash · deepseek
```

状态标记：`[ ]` 待办、`[~]` 进行中、`[x]` 已完成。待办清单以会话为作用域，并与子 Agent 共享；可用 `todos_update` 随任务推进更新条目状态。选择器（`/connect`、`/checkpoints`）与命令展示（`/help`、`/models` 等）打开时会优先占据底部区域，暂时隐藏待办清单。

### 自动 git 仓库与提交

启动会话时，rp 会在工作区根目录（`ROOT_DIR`）自动初始化 git 仓库（若尚未初始化），并在每轮对话结束后自动创建一个提交，把每一轮的改动都留档。

- 首次初始化时会写入一份安全的 `.gitignore`（仅当不存在时），避免 `.env`、`.rp/`、`log/` 等敏感或运行时产物被提交，随后创建初始基线提交。
- 每轮对话结束后（含被打断或出错的一轮），会把工作区全部改动暂存并提交，提交信息形如 `rp: 第 N 轮对话 - <摘要>`；没有实际改动时不会产生空提交。
- 若 git 不可用或命令执行失败，只记录日志并静默跳过，绝不影响正常对话。
- 在 `.env` 中设置 `RP_AUTO_GIT=0` 可关闭该功能。

#### 检查点与回滚

rp 创建的每一个提交（初始基线、每轮提交、任务分支提交、合并提交）都会把完整 hash 记录到 `.rp/checkpoints.json`：

- `/checkpoints` 打开可视化检查点选择器（非终端模式下列出清单）；`/checkpoints <hash>` 或 `/rollback <hash>` 直接指定目标提交。
- 选中检查点后 rp 会先请求确认，再执行 `git reset --hard <hash>` 把工作区回滚到该状态；输入 `y` 确认，其他任意键取消。

#### 任务分支（子 Agent 委派）

每个委派给子 Agent 的任务（`delegate` 工具）都会在独立分支上执行：

1. rp 先暂存当前未提交的改动，从当前 HEAD 创建分支 `task/<agent>-<时间戳>`。
2. 子 Agent 在该分支上执行，其改动提交到该分支。
3. 子 Agent 完成后，rp 展示改动统计并请你审核：输入 `y` 把分支合并回主分支（`--no-ff`，随后删除分支），输入 `n` 放弃该分支的全部改动；委派前暂存的改动会自动恢复。

### 多供应商与模型（JSON）

供应商配置使用 JSON 文件存储，不再使用环境变量：

- **预设模板**：`src/data/providers/preset/<名称>.json`，只含 `type` / `api_url` / `models` / `default_model`，不含 API Key，随项目分发。
- **使用预设**：运行 `rp` 后输入 `/connect`，底部固定区域会展示可用供应商列表，用 `↑ ↓` 切换、`Enter` 确认，随后提示输入 API Key，程序自动生成 `src/data/providers/<名称>.json`（预设元信息 + `api_key`）并切换。
- **当前选中**：`/connect`、`/models` 切换的 provider/model 会持久化到 `.rp/config.json`，下次启动自动恢复。

`type` 决定底层传输后端：

| `type` | 后端 | 说明 |
| --- | --- | --- |
| `openai` | OpenAI SDK → `chat.completions` | OpenAI 兼容供应商的默认选项（DeepSeek、GLM、Kimi、Qwen、MiniMax 等） |
| `responses` | OpenAI SDK → `responses` | OpenAI Responses API；`system` 转 `instructions`，工具结果转 `function_call_output` 项 |
| `anthropic` | `anthropic` SDK → `messages.stream` | Anthropic Claude；`system` 是独立参数，`max_tokens` 必填（默认 8192） |

`type` 缺失或非法时，该 provider 配置文件会被直接拒绝（`Config.validate()` 会给出明确提示）。

手动创建 `src/data/providers/<名称>.json` 示例：

```json
{
    "name": "deepseek",
    "type": "openai",
    "api_url": "https://api.deepseek.com/v1",
    "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    "default_model": "deepseek-v4-flash",
    "api_key": "sk-xxx"
}
```

Anthropic 示例：

```json
{
    "name": "anthropic",
    "type": "anthropic",
    "api_url": "https://api.anthropic.com",
    "models": ["claude-opus-4-1", "claude-sonnet-4-5", "claude-haiku-4-5"],
    "default_model": "claude-sonnet-4-5",
    "api_key": "sk-ant-xxx"
}
```

其余设置仍在 `.env` 中配置：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `RP_VARIANT` | 思考强度（`low` / `medium` / `high` / `max`） | `medium` |
| `RP_MODE` | 工作模式（`plan` / `build` / `auto`） | `auto` |
| `SEARCH_BACKEND` | 网页搜索后端（`bing` / `ddg` / `auto`，`auto` 表示 ddg 失败时回退 bing） | `bing` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_DIR` | 日志目录 | `log/` |
| `RP_AUTO_GIT` | 启动时自动初始化 git 仓库并在每轮对话后提交（`1` / `0`） | `1` |
| `LOG_ENCODING` | 日志文件编码 | `utf-8` |
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
cost_map.json            # 主流模型价格参考（元 / 1M tokens）
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

GitHub Actions 内置 `Release` / `Snapshot` 工作流：

- `Release`：推送 `v*` 标签触发，先跑测试，再在 Windows / Linux / macOS 三平台用 Nuitka 构建，随后把三平台二进制与 `src/data` 一起打包为 `rp-<标签>.zip` 并生成 `SHA256SUMS` 校验文件，最终创建 GitHub Release；带 `-alpha` / `-beta` 的标签只发布源码快照 pre-release，不构建二进制。
- `Snapshot`：每周一自动（或手动）创建 `snapshot-YYMMDD` 源码 pre-release。

## 贡献

欢迎提交 Issue 与 Pull Request，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
