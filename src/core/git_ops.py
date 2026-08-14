"""会话 git 工作区操作：自动仓库、检查点记录、回滚与任务分支。

- 会话启动时在工作区根目录（ROOT_DIR）自动 `git init`（若尚未初始化），
  写入安全的 .gitignore（仅当仓库由本功能新建且没有 .gitignore 时），
  随后创建初始基线提交。
- 每轮对话结束、任务分支提交、合并提交都会记录到 `.rp/checkpoints.json`
  （含完整 hash），供 /checkpoints 可视化浏览与 /rollback 回滚。
- 委派给子 Agent 的任务会先创建独立任务分支，完成后询问用户审核，
  通过则合并回主分支，否则丢弃分支改动。
- 所有 git 操作失败都只记录日志并静默跳过，绝不影响正常对话。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

_GIT_TIMEOUT_SECONDS = 30
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_RP_DIR_NAME = ".rp"
_CHECKPOINT_FILE_NAME = "checkpoints.json"

# 自动初始化仓库时写入的安全忽略规则（仅当 .gitignore 不存在时）
_AUTO_GITIGNORE = """\
# 由 rp Co-Pilot 自动生成：会话初始化 git 仓库时的安全忽略规则
.env
.env.*
*.local
.rp/
log/
logs/
*.log
__pycache__/
*.py[cod]
.venv/
venv/
env/
node_modules/
dist/
build/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.idea/
.vscode/
.DS_Store
Thumbs.db
"""

_INITIAL_COMMIT_MESSAGE = "chore: 初始化 git 仓库（rp 会话启动）"
_LOCAL_USER_NAME = "rp-co-pilot"
_LOCAL_USER_EMAIL = "rp-co-pilot@local"

_checkpoint_lock = threading.Lock()


# ---------- 基础 git 调用 ----------


def git_available() -> bool:
    """检查 git 可执行文件是否可用。"""
    return shutil.which("git") is not None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """在 root 目录执行 git 命令；执行失败（如 git 缺失/超时）返回 None。"""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("git 命令执行失败: git %s", " ".join(args), exc_info=True)
        return None


def _git_ok(result: subprocess.CompletedProcess[str] | None) -> bool:
    return result is not None and result.returncode == 0


def rev_parse(root: Path, ref: str) -> str | None:
    """解析任意引用/提交为完整 hash；无效返回 None。"""
    result = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if _git_ok(result):
        return (result.stdout or "").strip()
    return None


def current_branch(root: Path) -> str | None:
    """当前分支名；无分支（未提交状态）返回 None。"""
    result = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if _git_ok(result):
        name = (result.stdout or "").strip()
        if name and name != "HEAD":
            return name
    return None


def branch_exists(root: Path, name: str) -> bool:
    return rev_parse(root, f"refs/heads/{name}") is not None


def has_changes(root: Path) -> bool:
    """工作区是否有未提交改动（含未跟踪文件）。"""
    result = _git(root, "status", "--porcelain")
    return _git_ok(result) and bool((result.stdout or "").strip())


# ---------- 检查点记录 ----------


class CheckpointStore:
    """rp 记录的提交检查点（.rp/checkpoints.json，最新的在前）。"""

    def __init__(self, root: Path) -> None:
        self._file = root / _RP_DIR_NAME / _CHECKPOINT_FILE_NAME

    def record(
        self,
        commit_hash: str,
        message: str,
        kind: str = "round",
        round_no: int | None = None,
    ) -> None:
        if not commit_hash:
            return
        with _checkpoint_lock:
            entries = self._read()
            if any(entry.get("hash") == commit_hash for entry in entries):
                return
            entries.insert(
                0,
                {
                    "hash": commit_hash,
                    "short": commit_hash[:12],
                    "message": message,
                    "kind": kind,
                    "round": round_no,
                    "created_at": datetime.now().strftime(_TIME_FORMAT),
                },
            )
            self._write(entries)

    def list(self) -> list[dict]:
        with _checkpoint_lock:
            return self._read()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        entries = data.get("checkpoints", [])
        return [entry for entry in entries if isinstance(entry, dict)]

    def _write(self, entries: list[dict]) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps({"checkpoints": entries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("写入检查点文件失败", exc_info=True)


def record_checkpoint(
    root: Path,
    commit_hash: str,
    message: str,
    kind: str = "round",
    round_no: int | None = None,
) -> None:
    """记录一个由 rp 创建的提交。"""
    CheckpointStore(root).record(commit_hash, message, kind=kind, round_no=round_no)


# ---------- 仓库初始化与提交 ----------


def ensure_repo(root: Path) -> bool:
    """确保 root 目录是一个可用的 git 仓库。

    尚未初始化时自动 `git init`，写入安全 .gitignore、补齐仓库本地身份
    并创建初始基线提交。返回是否处于可用（已初始化）状态。
    """
    if not git_available():
        logger.warning("未检测到 git 可执行文件，跳过自动初始化仓库")
        return False
    created = False
    if not (root / ".git").exists():
        result = _git(root, "init")
        if not _git_ok(result):
            stderr = (result.stderr if result else "").strip()
            logger.warning("git init 失败：%s", stderr or "未知原因")
            return False
        created = True
        logger.info("已在 %s 初始化 git 仓库", root)
    if created:
        _write_safety_gitignore(root)
        _ensure_local_identity(root)
        _initial_commit(root)
    return True


def commit_changes(
    root: Path,
    message: str,
    kind: str = "round",
    round_no: int | None = None,
) -> str | None:
    """把当前工作区改动提交为一个新提交，返回完整 hash。

    无任何改动（或全部改动被忽略）时跳过并返回 None；提交成功会记录检查点。
    若因缺少身份配置失败，会补齐仓库本地身份后重试一次。
    """
    if not git_available():
        logger.warning("未检测到 git 可执行文件，跳过自动提交")
        return None
    add_result = _git(root, "add", "-A")
    if not _git_ok(add_result):
        logger.warning("git add 失败：%s", (add_result.stderr if add_result else "").strip())
        return None
    # git diff --cached --quiet：退出码 0 表示没有任何暂存改动
    staged = _git(root, "diff", "--cached", "--quiet")
    if _git_ok(staged):
        return None
    result = _git(root, "commit", "-m", message)
    if not _git_ok(result):
        _ensure_local_identity(root)
        result = _git(root, "commit", "-m", message)
        if not _git_ok(result):
            stderr = (result.stderr if result else "").strip()
            logger.warning("git commit 失败：%s", stderr or "未知原因")
            return None
    commit_hash = rev_parse(root, "HEAD")
    if commit_hash:
        record_checkpoint(root, commit_hash, message, kind=kind, round_no=round_no)
    logger.info("已提交：%s（%s）", message, commit_hash or "?")
    return commit_hash


def rollback_to(root: Path, commit: str) -> tuple[bool, str]:
    """回滚到指定提交（git reset --hard <commit>），返回 (是否成功, 说明)。"""
    if not git_available():
        return False, "git 不可用，无法回滚"
    target = rev_parse(root, commit)
    if target is None:
        return False, f"无效的提交：{commit}"
    result = _git(root, "reset", "--hard", commit)
    if not _git_ok(result):
        stderr = (result.stderr if result else "").strip()
        return False, stderr or "git reset 失败"
    head = rev_parse(root, "HEAD") or target
    return True, f"已回滚到 {head[:12]}（{commit}）"


# ---------- 仓库初始化辅助 ----------


def _write_safety_gitignore(root: Path) -> None:
    """为新建的仓库写入安全 .gitignore（已有文件时不覆盖）。"""
    gitignore = root / ".gitignore"
    if gitignore.exists():
        return
    try:
        gitignore.write_text(_AUTO_GITIGNORE, encoding="utf-8")
        logger.info("已生成安全 .gitignore（%s）", gitignore)
    except OSError:
        logger.debug("写入 .gitignore 失败", exc_info=True)


def _ensure_local_identity(root: Path) -> None:
    """补齐仓库本地的 user.name / user.email，避免 commit 失败。

    仅设置仓库级配置（不污染全局配置）；若用户已配置（全局或本地）则跳过。
    """
    if not _git_config_has(root, "user.name"):
        _git(root, "config", "user.name", _LOCAL_USER_NAME)
    if not _git_config_has(root, "user.email"):
        _git(root, "config", "user.email", _LOCAL_USER_EMAIL)


def _git_config_has(root: Path, key: str) -> bool:
    result = _git(root, "config", "--get", key)
    return _git_ok(result) and bool((result.stdout if result else "").strip())


def _initial_commit(root: Path) -> None:
    """创建初始基线提交（仅当仓库还没有任何提交时）。"""
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if _git_ok(head):
        return
    if not _git_ok(_git(root, "add", "-A")):
        return
    result = _git(root, "commit", "-m", _INITIAL_COMMIT_MESSAGE)
    if _git_ok(result):
        commit_hash = rev_parse(root, "HEAD")
        if commit_hash:
            record_checkpoint(root, commit_hash, _INITIAL_COMMIT_MESSAGE, kind="init")
        logger.info("已创建初始基线提交")
    else:
        logger.warning("初始基线提交失败（忽略，首次轮次提交会兜底）")


# ---------- 分支 / 暂存 / 合并 ----------


def checkout_branch(root: Path, name: str, create: bool = False) -> bool:
    """切换分支；create=True 时从当前 HEAD 创建新分支并切换。"""
    args = ["checkout"] + (["-b"] if create else []) + [name]
    return _git_ok(_git(root, *args))


def delete_branch(root: Path, name: str) -> bool:
    """强制删除分支。"""
    return _git_ok(_git(root, "branch", "-D", name))


def merge_branch(root: Path, name: str, message: str) -> str | None:
    """把指定分支以 --no-ff 方式合并到当前分支，返回合并提交 hash；失败返回 None。"""
    result = _git(root, "merge", "--no-ff", name, "-m", message)
    if not _git_ok(result):
        stderr = (result.stderr if result else "").strip()
        logger.warning("合并分支 %s 失败：%s", name, stderr or "未知原因")
        return None
    return rev_parse(root, "HEAD")


def stash_push(root: Path, message: str) -> bool:
    """暂存未提交改动（含未跟踪文件）；有改动被暂存返回 True。"""
    if not has_changes(root):
        return False
    return _git_ok(_git(root, "stash", "push", "-u", "-m", message))


def stash_pop(root: Path) -> bool:
    """恢复最近一次暂存；成功返回 True。"""
    return _git_ok(_git(root, "stash", "pop"))


def diff_stat(root: Path, base: str, head: str) -> str:
    """base..head 的改动统计（--stat 输出），无改动返回空串。"""
    result = _git(root, "diff", "--stat", f"{base}..{head}")
    return (result.stdout or "").strip() if result else ""


def setup_task_branch(root: Path, agent_id: str) -> dict | None:
    """为委派任务创建独立分支。

    先暂存主分支上未提交的改动，再从当前 HEAD 创建 task/<agent>-<时间戳> 分支。
    返回上下文 dict（含主分支名、分支名、是否暂存）；失败返回 None。
    """
    if not git_available():
        return None
    if not ensure_repo(root):
        return None
    main_branch = current_branch(root)
    if main_branch is None:
        logger.warning("仓库还没有提交，无法创建任务分支")
        return None
    stashed = stash_push(root, f"rp: 委派 {agent_id} 前暂存")
    branch = f"task/{agent_id}-{int(time.time())}"
    if not checkout_branch(root, branch, create=True):
        if stashed:
            stash_pop(root)
        logger.warning("创建任务分支 %s 失败", branch)
        return None
    logger.info("已为子 Agent %s 创建任务分支 %s（主分支 %s）", agent_id, branch, main_branch)
    return {"main": main_branch, "branch": branch, "stashed": stashed}


def finish_task_branch(root: Path, ctx: dict, agent_id: str, ask: Callable[[str], str]) -> bool:
    """子 Agent 完成后：提交分支改动 → 询问用户审核 → 合并或放弃。

    返回是否合并回主分支。ask 用于向用户提问（如 y/n 审核），返回用户回答。
    """
    main_branch = ctx["main"]
    branch = ctx["branch"]
    try:
        if has_changes(root):
            commit_changes(root, f"rp: 任务分支 {branch} - {agent_id}", kind="task")
        stat = diff_stat(root, main_branch, branch)
        if not stat:
            stat = "（无文件改动）"
        question = (
            f"子 Agent {agent_id} 已完成，改动位于分支 {branch}：\n{stat}\n"
            f"输入 y 合并回 {main_branch}，输入 n 放弃该分支的改动"
        )
        answer = (ask(question) or "").strip().lower()
        if answer in {"y", "yes"}:
            if rev_parse(root, branch) == rev_parse(root, main_branch):
                # 分支没有新提交（子 Agent 未改动任何文件），无需合并
                checkout_branch(root, main_branch)
                delete_branch(root, branch)
                return False
            if not checkout_branch(root, main_branch):
                return False
            merged = merge_branch(root, branch, f"merge: 合并 {agent_id} 任务分支 {branch}")
            delete_branch(root, branch)
            if merged:
                record_checkpoint(root, merged, f"merge: {agent_id} 任务分支 {branch}", kind="merge")
                logger.info("已合并任务分支 %s 回 %s", branch, main_branch)
                return True
            return False
        checkout_branch(root, main_branch)
        delete_branch(root, branch)
        logger.info("用户未批准，已放弃任务分支 %s", branch)
        return False
    finally:
        if ctx.get("stashed"):
            if not stash_pop(root):
                logger.warning("恢复暂存失败，可手动执行 git stash pop")


def abort_task_branch(root: Path, ctx: dict | None) -> None:
    """异常/失败时清理任务分支：回到主分支、删除分支、恢复暂存。"""
    if ctx is None:
        return
    try:
        if current_branch(root) != ctx["main"]:
            checkout_branch(root, ctx["main"])
            delete_branch(root, ctx["branch"])
    except Exception:
        logger.debug("清理任务分支失败", exc_info=True)
    finally:
        if ctx.get("stashed"):
            stash_pop(root)
