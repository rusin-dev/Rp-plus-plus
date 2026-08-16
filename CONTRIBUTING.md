# 贡献指南

感谢你愿意参与本项目！请遵循以下规范。

## 分支与提交

* `master` 是保护分支，请先创建 feature 分支，通过 Pull Request 合入 `dev` 分支。
* 提交信息使用简洁的英文或中文，描述变更意图。
* 创建 PR 前请先创建对应的 Issue ，并把具体修改和期望放在 Issue 而不是 PR 中。

## 代码规范

* 本仓库使用 [ruff](https://docs.astral.sh/ruff/) 做代码检查（配置见 `pyproject.toml`）。
* 提交前请确保通过：

  ```bash
  pip install -r requirements-dev.txt
  ruff check .
  ```

* 新功能请附带对应的 pytest 测试，并确保全部通过：

  ```bash
  pytest
  ```

* 请勿提交 `.env`、`src/data/providers/*.json` 等敏感配置（已加入 `.gitignore`）。

## 目录约定

* 项目采用三层架构：`src/core`（基础设施）、`src/api`（能力）、`src/ui`（表现）。
* 依赖方向必须单向：`ui` → `api` → `core`，禁止反向依赖（core 不得 import api/ui）。
* 提示词文件放在 `src/data/<level>/` 目录，`.md` 或 `.txt` 格式。
* 新增跨线程通信请复用 `core/event_bus.py` 的事件类型，不要直接操作线程/队列。
