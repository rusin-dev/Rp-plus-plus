# rp++

<div align="center">
<img src="https://github.com/rusin-dev/Rp-plus-plus/blob/master/image.png">
</div>

[简体中文](https://github.com/rusin-dev/Rp-plus-plus/blob/master/.docs/README.zh.md)

## Introduction

This is a Python-based command-line AI coding assistant. It uses system prompts to constrain the model to play the role of Project Pilot (a senior project engineer), turning vague user intent into a clear execution blueprint. It supports streaming output, interactive conversation, live in-terminal Markdown rendering, multi-provider switching, session recovery, and sub-agent domain delegation.

## Quick Start

```bash
# Clone the repository
git clone git@gitee.com:mian-dev/rp--your-programming-co-pilot.git
cd rp--your-programming-co-pilot

# Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Install as an executable command
pip install -e .

# Configure the API key
cp .env.example .env
# Edit .env and fill in non-provider settings (logging, etc.)
# Provider configuration uses JSON: run rp, enter /connect to pick a preset and enter your API key
#   (see "Multi-provider and Models" below)
```

Once installed, use the `rp` command (equivalent to `python -m src.main`).

## Usage

```bash
# Single question
python -m src.main -m "Help me design a user login module"

# Enter interactive mode (type exit/quit/q to quit)
python -m src.main

# Specify a different prompt file
python -m src.main -p SYSTEM_PROMPT.md -l general

# Start in a specific working mode (plan / build / auto)
python -m src.main -M plan -m "Help me design a user login module"

# List available prompt files
python -m src.main --list-prompts
```

### Working Modes

| Mode | Description |
| --- | --- |
| `plan` | Planning only, no file modification (defensively disables the `shell` / `write` / `edit` tools) |
| `build` | Implement the requirements directly |
| `auto` | Automatically plan and implement (default) |

- In interactive mode, enter `/mode` to view/switch, or press `Shift+Tab` to cycle;
- On the command line, use `-M/--mode <mode>` to specify the startup mode.

### Sub-Agents (Domain Delegation)

Project Pilot has 5 built-in sub-agents, automatically delegated domain-specific tasks via the `delegate` tool:

| Sub-Agent | Responsibility |
| --- | --- |
| `librarian` | Knowledge retrieval and material organization |
| `frontend_builder` | Frontend code implementation |
| `backend_builder` | Backend code implementation |
| `ui_ux_designer` | UI/UX design |
| `reviewer` | Code review and quality assurance |

Each sub-agent has its own prompt (`src/data/agents/`, with frontmatter declaring the role description and tool allowlist) and an independent LLM invocation loop. Execution is displayed live in the terminal, with mouse-click collapsible panels. Sub-agents neither ask the user questions nor delegate again.

### Slash Commands (Interactive Mode)

When input starts with `/`, a command suggestion box appears automatically: use `↑/↓` or `Tab` to switch candidates, `Enter` to confirm, and `Esc` to close. You can also type the full command and press Enter to run it.

| Command | Description |
| --- | --- |
| `/help` | Show all available commands |
| `/variants` | View/switch thinking intensity (`low` / `medium` / `high` / `max`, passed to the API as `reasoning_effort`) |
| `/models` | List the current provider's available models; `/models <name>` switches |
| `/connect` | List configured providers; `/connect <name>` switches |
| `/mode` | View/switch working mode (`plan` / `build` / `auto`) |
| `/compact` | Compact the conversation context (keeps the last 20 messages; `/compact <n>` to specify) |
| `/usage` | View token usage and context window occupancy |
| `/init` | Generate `AGENTS.md` in the workspace root (`/init -f` overwrites an existing file) |
| `/session` | List saved sessions; `/session <id>` resumes the specified session and continues the conversation |
| `/clear` | Clear conversation history |
| `/exit` / `/quit` | Quit |

Conversations are automatically saved to `.rp/sessions/` (already added to `.gitignore`); on next launch, use `/session` to restore context.

### Auto Git Repository & Commits

When a session starts, rp automatically initializes a git repository in the workspace root (`ROOT_DIR`) if it is not already one, and creates a commit after each completed round of conversation so every round's changes are snapshotted.

- On first initialization, a safe `.gitignore` is written (only when none exists) to keep secrets and runtime artifacts — such as `.env`, `.rp/`, `log/` — out of version control, followed by an initial baseline commit.
- After each round (including interrupted or errored ones), all workspace changes are staged and committed with a message like `rp: 第 N 轮对话 - <summary>`. Empty commits are never created.
- Commits only happen when something actually changed; if git is unavailable or a command fails, it is logged and silently skipped — the conversation is never affected.
- Set `RP_AUTO_GIT=0` in `.env` to disable this feature.

#### Checkpoints & Rollback

Every commit rp creates (initial baseline, per-round, task branches, merges) is recorded with its full hash into `.rp/checkpoints.json`:

- `/checkpoints` opens a visual checkpoint picker (or lists them in non-terminal mode); `/checkpoints <hash>` or `/rollback <hash>` targets a specific commit directly.
- After selecting a checkpoint, rp asks for confirmation, then executes `git reset --hard <hash>` to roll the workspace back to that state. Confirm with `y`, cancel with any other key.

#### Task Branches (Sub-Agent Delegation)

Each task delegated to a sub-agent (via the `delegate` tool) runs on its own branch:

1. rp stashes any uncommitted changes and creates a branch `task/<agent>-<timestamp>` from the current HEAD.
2. The sub-agent executes on that branch; its work is committed there.
3. When it finishes, rp shows the change statistics and asks you to review: input `y` to merge the branch back to the main branch (`--no-ff`, then the branch is deleted), or `n` to discard the branch's changes. Any changes stashed before the delegation are restored afterwards.

### Multi-provider and Models (JSON)

Provider configuration is stored in JSON files rather than environment variables:

- **Preset templates**: `src/data/providers/preset/<name>.json`, containing only `api_url` / `models` / `default_model` (no API key), distributed with the project.
- **Using a preset**: run `rp`, then enter `/connect`. The fixed bottom area shows the list of available providers; use `↑ ↓` to switch and `Enter` to confirm, then you are prompted to enter the API key. The program automatically generates `src/data/providers/<name>.json` (preset metadata + `api_key`) and switches to it.
- **Current selection**: the provider/model selected via `/connect` and `/models` is persisted to `.rp/config.json` and restored automatically on next launch.

Example of manually creating `src/data/providers/<name>.json`:

```json
{
    "name": "deepseek",
    "api_url": "https://api.deepseek.com/v1",
    "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    "default_model": "deepseek-v4-flash",
    "api_key": "sk-xxx"
}
```

Other settings are still configured in `.env`:

| Variable | Description | Default |
| --- | --- | --- |
| `RP_VARIANT` | Thinking intensity (`low` / `medium` / `high` / `max`) | `medium` |
| `RP_MODE` | Working mode (`plan` / `build` / `auto`) | `auto` |
| `SEARCH_BACKEND` | Web search backend (`bing` / `ddg` / `auto`; `auto` falls back to bing when ddg fails) | `bing` |
| `LOG_LEVEL` | Log level | `INFO` |
| `LOG_DIR` | Log directory | `log/` |
| `LOG_ENCODING` | Log file encoding | `utf-8` |
| `SESSION_DIR` | Session storage directory | `.rp/sessions/` |
| `RP_AUTO_GIT` | Auto-initialize a git repo at session start and commit after each round (`1` / `0`) | `1` |
| `RICH_COLOR_SYSTEM` | Terminal color system (`auto` / `standard` / `256` / `truecolor` / `windows`) | `auto` |
| `RICH_THEME` | rich theme | none |
| `TAB_SIZE` | Tab width | `8` |

## Directory Structure

The project uses a three-layer architecture: `core` (infrastructure) → `api` (capabilities) → `ui` (presentation).

```
src/
├── main.py              # Entry point: assembles the three layers and starts
├── config.py            # Configuration and validation (JSON provider presets / mode / variant)
├── core/                # Infrastructure layer
│   ├── logger.py        # Logging (file + console)
│   ├── event_bus.py     # Event bus (inter-thread communication)
│   ├── prompt.py        # Prompt loading
│   └── session.py       # Session persistence (JSON save / load / restore)
├── api/                 # Capability layer
│   ├── client.py        # OpenAI client (background thread + tool invocation loop)
│   ├── agents.py        # Sub-agent definition loading and independent run loop
│   └── tools.py         # Tool definitions (schemas) and executors
├── ui/                  # Presentation layer
│   ├── app.py           # rich TUI (Live rendering + event consumption + session recovery)
│   ├── input.py         # Input box (slash command completion / mode badge / key bindings)
│   ├── cancel_watcher.py# Background listener for double-ESC, triggers a CANCEL event to interrupt the current answer
│   ├── formatters.py    # Compresses tool call arguments into readable name(args) display text
│   ├── mascot.py        # Startup mascot
│   └── subagent_panel.py# Sub-agent execution panel (live display / collapsible)
├── data/general/        # System prompts
├── data/agents/         # Sub-agent prompts (frontmatter declares roles and tool permissions)
├── data/providers/preset/ # Provider preset templates (JSON, no API key)
└── data/providers/      # Provider configs generated after using presets (contains API key)
scripts/
├── build_exe.py         # One-click Nuitka compilation of a single-file executable
└── launcher.py          # Packaging entry point (forwards to src.main:main)
tests/                   # pytest tests (core / api / ui / agents / session, etc.)
.github/workflows/       # GitHub Actions: CI / Format / Release / Snapshot / Auto Merge
pyproject.toml           # Project metadata, ruff and pytest config, `rp` command entry point
cost_map.json            # Reference pricing for mainstream models (CNY / 1M tokens)
```

### Layer Responsibilities

| Layer | Responsibility | Dependencies |
| --- | --- | --- |
| `core` | Logging, inter-thread communication (event bus), prompt loading, session persistence | Standard library + config only |
| `api` | OpenAI requests, streaming output, tool definition and execution, sub-agent running | core |
| `ui` | rich TUI: rendering messages, input interaction, sub-agent panels, consuming events | core + api |

Built-in tools: `ask` (ask the user a question, via the event bus), `read` (read a workspace file), `write` (write a workspace file, content previewed in a code box), `edit` (precise replacement in an existing file, changes shown as a git-style diff), `grep` (regular-expression search), `shell` (execute commands), `web_search` (web search, Bing by default, switchable via `SEARCH_BACKEND`), `web_fetch` (fetch web page content), `delegate` (delegate domain-specific tasks to a sub-agent). Tool call arguments are displayed in a readable form in the terminal instead of raw JSON. Read/write tools are anchored to the workspace root by default to prevent out-of-bounds access.

Communication model: the UI main thread handles rendering and input; API requests run on a background thread and publish token / tool call / sub-agent event / error events via the `EventBus`, which the UI consumes to update the interface in real time. The `ask` tool uses the bus to ask the UI a question and wait for the user's answer, forming a complete closed loop.

## Development

```bash
pip install -r requirements-dev.txt
ruff check .            # Lint
ruff format .           # Format
pytest                  # Run tests (CI covers Python 3.10 ~ 3.13)
```

## Packaging & Release

Use [Nuitka](https://nuitka.net/) to compile the project into a single-file executable (not cross-platform; build separately on each target system):

```bash
# Full build (output in dist/: Windows -> rp.exe, Linux/macOS -> rp)
python scripts/build_exe.py

# Preview the Nuitka command that would run
python scripts/build_exe.py --dry-run
```

GitHub Actions includes `Release` / `Snapshot` workflows:

- `Release`: triggered by pushing a `v*` tag. It runs tests first, then builds with Nuitka on Windows / Linux / macOS, packs the three platform binaries together with `src/data` into `rp-<tag>.zip`, generates a `SHA256SUMS` checksum file, and finally creates a GitHub Release. Tags with `-alpha` / `-beta` only publish a source snapshot pre-release and do not build binaries.
- `Snapshot`: automatically (or manually) creates a `snapshot-YYMMDD` source pre-release every Monday.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is licensed under the [MIT License](LICENSE).
