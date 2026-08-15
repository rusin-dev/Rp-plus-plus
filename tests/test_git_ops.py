import shutil
import subprocess

import pytest

from src.core.git_ops import (
    CheckpointStore,
    abort_task_branch,
    branch_exists,
    commit_changes,
    current_branch,
    diff_stat,
    ensure_repo,
    finish_task_branch,
    git_available,
    has_changes,
    rev_parse,
    rollback_to,
    setup_task_branch,
    stash_pop,
    stash_push,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 不可用，跳过 git 集成测试"
)


def _run_git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _log_messages(root):
    result = _run_git(root, "log", "--format=%s")
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def _write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------- 基础能力 ----------


def test_git_available():
    assert git_available() is (shutil.which("git") is not None)


def test_ensure_repo_initializes(tmp_path):
    assert ensure_repo(tmp_path) is True
    assert (tmp_path / ".git").exists()
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".rp/" in gitignore
    assert ".env" in gitignore
    assert "src/data/providers/*.json" in gitignore
    assert _log_messages(tmp_path) == ["chore: 初始化 git 仓库（rp 会话启动）"]


def test_ensure_repo_idempotent(tmp_path):
    assert ensure_repo(tmp_path) is True
    assert ensure_repo(tmp_path) is True
    assert len(_log_messages(tmp_path)) == 1


def test_ensure_repo_keeps_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("custom-ignore\n", encoding="utf-8")
    assert ensure_repo(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "custom-ignore\n"


def test_ensure_repo_upgrades_existing_rp_gitignore(tmp_path):
    """旧版 rp 自动生成的 .gitignore 缺少 provider 忽略规则时，应幂等补齐。"""
    from src.core.git_ops import _AUTO_GITIGNORE

    old_lines = [
        line
        for line in _AUTO_GITIGNORE.splitlines()
        if line.strip() != "src/data/providers/*.json"
        and not line.strip().startswith(("# 生成的供应商配置", "# （否则委派子 Agent"))
    ]
    (tmp_path / ".gitignore").write_text("\n".join(old_lines) + "\n", encoding="utf-8")
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "add", ".gitignore")
    _run_git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    assert ensure_repo(tmp_path) is True
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "src/data/providers/*.json" in content
    # 幂等：再次 ensure 不重复追加
    assert ensure_repo(tmp_path) is True
    assert content.count("src/data/providers/*.json") == (
        tmp_path / ".gitignore"
    ).read_text(encoding="utf-8").count("src/data/providers/*.json")


def test_ensure_repo_skips_when_git_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.git_ops.shutil.which", lambda _: None)
    assert ensure_repo(tmp_path) is False
    assert not (tmp_path / ".git").exists()


def test_commit_changes_creates_commit(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "a.txt", "hello")
    commit_hash = commit_changes(tmp_path, "rp: 第 1 轮对话 - 测试")
    assert commit_hash is not None
    assert len(commit_hash) == 40
    assert rev_parse(tmp_path, "HEAD") == commit_hash
    assert _log_messages(tmp_path) == [
        "rp: 第 1 轮对话 - 测试",
        "chore: 初始化 git 仓库（rp 会话启动）",
    ]


def test_commit_changes_no_changes_skips(tmp_path):
    assert ensure_repo(tmp_path)
    assert commit_changes(tmp_path, "noop") is None
    assert len(_log_messages(tmp_path)) == 1


def test_commit_changes_skips_when_only_ignored_changed(tmp_path):
    assert ensure_repo(tmp_path)
    (tmp_path / ".rp" / "session.json").write_text("{}", encoding="utf-8")
    (tmp_path / "log").mkdir(exist_ok=True)
    (tmp_path / "log" / "x.log").write_text("x", encoding="utf-8")
    assert commit_changes(tmp_path, "ignored only") is None
    assert len(_log_messages(tmp_path)) == 1


def test_commit_changes_after_more_edits(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "a.txt", "v1")
    assert commit_changes(tmp_path, "rp: 第 1 轮对话 - 第一轮") is not None
    _write(tmp_path, "a.txt", "v2")
    _write(tmp_path, "b.txt", "b")
    assert commit_changes(tmp_path, "rp: 第 2 轮对话 - 第二轮") is not None
    assert _log_messages(tmp_path) == [
        "rp: 第 2 轮对话 - 第二轮",
        "rp: 第 1 轮对话 - 第一轮",
        "chore: 初始化 git 仓库（rp 会话启动）",
    ]


def test_commit_changes_git_missing(tmp_path, monkeypatch):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "a.txt", "x")
    monkeypatch.setattr("src.core.git_ops.shutil.which", lambda _: None)
    assert commit_changes(tmp_path, "noop") is None


def test_commit_changes_retries_with_local_identity(tmp_path, monkeypatch):
    """用户已有仓库但缺少身份配置时，自动补齐本地身份并成功提交。"""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "home" / ".gitconfig"))
    _run_git(tmp_path, "init")
    _write(tmp_path, "a.txt", "x")
    assert commit_changes(tmp_path, "rp: 第 1 轮对话 - 无身份仓库") is not None
    assert len(_log_messages(tmp_path)) == 1
    name = _run_git(tmp_path, "config", "--local", "--get", "user.name")
    assert name.stdout.strip() == "rp-co-pilot"


# ---------- 检查点记录 ----------


def test_checkpoint_store_record_list_and_dedupe(tmp_path):
    store = CheckpointStore(tmp_path)
    store.record("a" * 40, "m1", kind="round", round_no=1)
    store.record("a" * 40, "m1-dup", kind="round")
    store.record("b" * 40, "m2", kind="init")
    entries = store.list()
    assert len(entries) == 2
    assert entries[0]["hash"] == "b" * 40
    assert entries[0]["short"] == "b" * 12
    assert entries[0]["kind"] == "init"
    assert entries[1]["round"] == 1


def test_ensure_repo_records_init_checkpoint(tmp_path):
    assert ensure_repo(tmp_path)
    entries = CheckpointStore(tmp_path).list()
    assert len(entries) == 1
    assert entries[0]["kind"] == "init"


def test_commit_records_round_checkpoint(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "a.txt", "x")
    commit_hash = commit_changes(tmp_path, "rp: 第 1 轮对话 - hi", kind="round", round_no=1)
    entries = CheckpointStore(tmp_path).list()
    assert entries[0]["hash"] == commit_hash
    assert entries[0]["kind"] == "round"
    assert entries[0]["round"] == 1


# ---------- 回滚 ----------


def test_rollback_to_previous_commit(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "a.txt", "v1")
    first = commit_changes(tmp_path, "rp: 第 1 轮对话 - v1")
    assert first is not None
    _write(tmp_path, "a.txt", "v2")
    _write(tmp_path, "b.txt", "b")
    second = commit_changes(tmp_path, "rp: 第 2 轮对话 - v2")
    assert second is not None
    ok, message = rollback_to(tmp_path, first)
    assert ok is True
    assert "已回滚" in message
    assert rev_parse(tmp_path, "HEAD") == first
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"
    assert not (tmp_path / "b.txt").exists()


def test_rollback_to_invalid_commit(tmp_path):
    assert ensure_repo(tmp_path)
    ok, message = rollback_to(tmp_path, "deadbeef" * 5)
    assert ok is False
    assert "无效的提交" in message


def test_rollback_keeps_checkpoints(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "a.txt", "x")
    commit_changes(tmp_path, "rp: 第 1 轮对话 - x")
    before = CheckpointStore(tmp_path).list()
    ok, _ = rollback_to(tmp_path, before[-1]["hash"])
    assert ok is True
    assert len(CheckpointStore(tmp_path).list()) == len(before)


# ---------- 分支 / 暂存 / 合并 ----------


def test_stash_roundtrip(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "untracked.txt", "x")
    assert has_changes(tmp_path) is True
    assert stash_push(tmp_path, "msg") is True
    assert has_changes(tmp_path) is False
    assert stash_pop(tmp_path) is True
    assert has_changes(tmp_path) is True


def test_stash_push_skips_ignored_provider_configs(tmp_path):
    """回归：含 API Key 的 provider 配置（src/data/providers/*.json）即使未提交，
    也不能被 git stash push -u 移出工作区——否则委派子 Agent 时 provider 解析为空、
    模型名变成空串，API 直接报 400 "you passed ."。"""
    assert ensure_repo(tmp_path)
    provider = tmp_path / "src" / "data" / "providers" / "deepseek.json"
    provider.parent.mkdir(parents=True, exist_ok=True)
    provider.write_text('{"api_key": "sk-test", "default_model": "deepseek-v4-flash"}', encoding="utf-8")
    assert stash_push(tmp_path, "rp: 委派前暂存") is False
    assert provider.exists()
    assert stash_pop(tmp_path) is False


def test_commit_changes_skips_provider_configs(tmp_path):
    """provider 配置（含 API Key）不应被自动提交进版本库。"""
    assert ensure_repo(tmp_path)
    provider = tmp_path / "src" / "data" / "providers" / "deepseek.json"
    provider.parent.mkdir(parents=True, exist_ok=True)
    provider.write_text('{"api_key": "sk-test"}', encoding="utf-8")
    assert commit_changes(tmp_path, "rp: 第 1 轮对话 - x") is None
    assert provider.exists()
    assert len(_log_messages(tmp_path)) == 1


def test_setup_task_branch_creates_branch(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "base.txt", "base")
    commit_changes(tmp_path, "base")
    main_before = current_branch(tmp_path)
    ctx = setup_task_branch(tmp_path, "reviewer")
    assert ctx is not None
    assert ctx["main"] == main_before
    assert ctx["branch"].startswith("task/reviewer-")
    assert current_branch(tmp_path) == ctx["branch"]
    assert branch_exists(tmp_path, ctx["branch"]) is True
    abort_task_branch(tmp_path, ctx)
    assert current_branch(tmp_path) == main_before
    assert branch_exists(tmp_path, ctx["branch"]) is False


def test_setup_task_branch_without_git_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.git_ops.shutil.which", lambda _: None)
    assert setup_task_branch(tmp_path, "x") is None


def test_finish_task_branch_merge_flow(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "base.txt", "base")
    commit_changes(tmp_path, "base")
    main_before = current_branch(tmp_path)
    ctx = setup_task_branch(tmp_path, "backend_builder")
    assert ctx is not None
    _write(tmp_path, "work.txt", "work")
    answers = []
    merged = finish_task_branch(
        tmp_path, ctx, "backend_builder", lambda q: (answers.append(q), "y")[1]
    )
    assert merged is True
    assert len(answers) == 1
    assert "backend_builder" in answers[0]
    assert "task/backend_builder-" in answers[0]
    assert current_branch(tmp_path) == main_before
    assert (tmp_path / "work.txt").read_text(encoding="utf-8") == "work"
    assert branch_exists(tmp_path, ctx["branch"]) is False
    kinds = [e["kind"] for e in CheckpointStore(tmp_path).list()]
    assert "task" in kinds
    assert "merge" in kinds


def test_finish_task_branch_reject_flow(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "base.txt", "base")
    commit_changes(tmp_path, "base")
    main_before = current_branch(tmp_path)
    ctx = setup_task_branch(tmp_path, "reviewer")
    assert ctx is not None
    _write(tmp_path, "work.txt", "work")
    merged = finish_task_branch(tmp_path, ctx, "reviewer", lambda q: "n")
    assert merged is False
    assert current_branch(tmp_path) == main_before
    # 分支改动被丢弃
    assert not (tmp_path / "work.txt").exists()
    assert branch_exists(tmp_path, ctx["branch"]) is False
    kinds = [e["kind"] for e in CheckpointStore(tmp_path).list()]
    assert "merge" not in kinds


def test_finish_task_branch_no_changes_skips_merge(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "base.txt", "base")
    commit_changes(tmp_path, "base")
    main_before = current_branch(tmp_path)
    ctx = setup_task_branch(tmp_path, "librarian")
    assert ctx is not None
    # 子 Agent 未改动任何文件
    merged = finish_task_branch(tmp_path, ctx, "librarian", lambda q: "y")
    assert merged is False
    assert current_branch(tmp_path) == main_before
    assert branch_exists(tmp_path, ctx["branch"]) is False
    assert diff_stat(tmp_path, main_before, main_before) == ""


def test_finish_task_branch_restores_stash(tmp_path):
    assert ensure_repo(tmp_path)
    _write(tmp_path, "base.txt", "base")
    commit_changes(tmp_path, "base")
    _write(tmp_path, "pending.txt", "pending")  # 主分支上未提交的改动
    ctx = setup_task_branch(tmp_path, "reviewer")
    assert ctx is not None
    assert ctx["stashed"] is True
    assert has_changes(tmp_path) is False  # 改动已暂存，任务分支是干净的
    merged = finish_task_branch(tmp_path, ctx, "reviewer", lambda q: "n")
    assert merged is False
    # 暂存被恢复
    assert (tmp_path / "pending.txt").read_text(encoding="utf-8") == "pending"
    assert has_changes(tmp_path) is True
