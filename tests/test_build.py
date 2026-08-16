import sys
from pathlib import Path

from src import config as config_module
from src.config import Config

# ---------- config.py 冻结路径适配 ----------


def test_non_frozen_paths_match_source_tree():
    root = config_module._resolve_root()
    assert (root / "src" / "config.py").is_file()
    assert config_module._resolve_data_dir() == root / "src" / "data"


def test_frozen_paths_anchor_to_executable_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "_FROZEN", True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.chdir(tmp_path)
    exe_dir = Path(sys.executable).resolve().parent
    # 打包后根目录锚定到 exe 所在目录（而非 CWD），日志/会话等跟随 exe
    assert config_module._resolve_root() == exe_dir
    # 捆绑进程序的数据资源仍从 _MEIPASS（PyInstaller）或 exe 旁读取
    assert config_module._resolve_data_dir() == tmp_path / "bundle" / "src" / "data"


def test_load_dotenv_uses_project_root_when_source(monkeypatch):
    calls = []
    monkeypatch.setattr(config_module, "load_dotenv", lambda path: calls.append(path))
    monkeypatch.setattr(config_module, "_FROZEN", False)
    config_module._load_dotenv()
    assert calls == [config_module._resolve_root() / ".env"]


def test_load_dotenv_uses_exe_dir_when_frozen(monkeypatch):
    calls = []
    monkeypatch.setattr(config_module, "load_dotenv", lambda path: calls.append(path))
    monkeypatch.setattr(config_module, "_FROZEN", True)
    config_module._load_dotenv()
    assert calls == [config_module._executable_dir() / ".env"]


def test_config_attrs_stable_in_non_frozen_env():
    assert Config.ROOT_DIR == config_module._resolve_root()
    assert Config.DATA_DIR == config_module._resolve_data_dir()


# ---------- build_exe.py 参数构造 ----------


def test_build_args_for_nuitka():
    if sys.platform == "win32":
        import pytest

        pytest.skip("Nuitka path not used on Windows")
    from scripts.build_exe import build_args

    args = build_args()
    assert "--onefile" in args
    assert "--standalone" in args
    assert "--assume-yes-for-downloads" in args
    assert any(a == "--output-filename=rp" for a in args)
    data_spec = next(a for a in args if a.startswith("--include-data-dir="))
    assert "src/data" in data_spec
    output_dir = next(a for a in args if a.startswith("--output-dir="))
    assert Path(output_dir.removeprefix("--output-dir=")).name == "dist"
    assert not any(a.startswith("--no-debug") for a in args)
    assert args[-1].endswith("launcher.py")


def test_build_args_for_pyinstaller():
    if sys.platform != "win32":
        import pytest

        pytest.skip("PyInstaller path only used on Windows")
    from scripts.build_exe import build_args

    args = build_args()
    assert "--onefile" in args
    assert any(a == "--name=rp" for a in args)
    data_spec = next(a for a in args if a.startswith("--add-data="))
    assert "src/data" in data_spec
    distpath = next(a for a in args if a.startswith("--distpath="))
    assert Path(distpath.removeprefix("--distpath=")).name == "dist"
    assert "--clean" in args
    assert "--noconfirm" in args
    assert args[-1].endswith("launcher.py")


def test_build_args_data_spec_targets_src_data():
    from scripts.build_exe import build_args

    args = build_args()
    if sys.platform == "win32":
        data_spec = next(a for a in args if a.startswith("--add-data=")).removeprefix("--add-data=")
        sep = ";"
    else:
        data_spec = next(a for a in args if a.startswith("--include-data-dir=")).removeprefix(
            "--include-data-dir="
        )
        sep = "="
    source, target = data_spec.split(sep)
    assert Path(source).is_dir()
    assert target == "src/data"


def test_build_main_dry_run():
    from scripts.build_exe import main

    assert main(["--dry-run"]) == 0


# ---------- 修复 Issue #20：避免打包本机所有 pip 库 ----------


def test_build_args_excludes_known_heavy_modules():
    """torch / jupyter 等本机常见干扰库必须显式排除，否则会被 PyInstaller/Nuitka 一并打进产物。"""
    from scripts.build_exe import EXCLUDED_MODULES, build_args

    args = build_args()
    if sys.platform == "win32":
        flag = "--exclude-module"
    else:
        flag = "--nofollow-import-to"
    excluded = [a for a in args if a.startswith(flag + "=")]
    assert len(excluded) == len(EXCLUDED_MODULES)
    for mod in EXCLUDED_MODULES:
        assert f"{flag}={mod}" in excluded


def test_venv_python_matches_platform_layout():
    """构建用 venv 的 Python 路径必须符合平台布局（Windows: Scripts\\python.exe；其它: bin/python）。"""
    from scripts.build_exe import _venv_python

    path = Path(_venv_python())
    if sys.platform == "win32":
        assert path.parts[-2:] == ("Scripts", "python.exe")
    else:
        assert path.parts[-2:] == ("bin", "python")


def test_ensure_clean_venv_uses_requirements_txt(monkeypatch, tmp_path):
    """干净 venv 必须从 requirements.txt 安装依赖，而不是从系统 Python 复制。"""
    from scripts import build_exe

    monkeypatch.setattr(build_exe, "ROOT", tmp_path)
    (tmp_path / "requirements.txt").write_text("dummy==0.0.0\n", encoding="utf-8")

    installed_with = []

    class _FakeEnvBuilder:
        def __init__(self, *args, **kwargs):
            pass

        def create(self, _path):
            base = Path(_path) / ("Scripts" if sys.platform == "win32" else "bin")
            base.mkdir(parents=True, exist_ok=True)
            (base / ("python.exe" if sys.platform == "win32" else "python")).touch()
            (Path(_path) / ".stamp").touch()

    monkeypatch.setattr(build_exe.venv, "EnvBuilder", _FakeEnvBuilder)
    monkeypatch.setattr(
        build_exe.subprocess,
        "check_call",
        lambda cmd, *a, **kw: installed_with.append(cmd),
    )

    build_exe._ensure_clean_venv()

    req_install = next(
        (
            c
            for c in installed_with
            if "-r" in c and str(tmp_path / "requirements.txt") in " ".join(map(str, c))
        ),
        None,
    )
    assert req_install is not None, "必须从 requirements.txt 安装依赖"


def test_main_uses_venv_python_when_running_build(monkeypatch):
    """实际执行打包时，必须用 venv 内的 Python 而不是当前解释器，避免本机 pip 库被打包。"""
    from scripts import build_exe

    captured = {}

    monkeypatch.setattr(build_exe, "_ensure_clean_venv", lambda: None)

    fake_venv_py = "/fake/venv/python"
    monkeypatch.setattr(build_exe, "_venv_python", lambda: fake_venv_py)

    def _fake_check_call(cmd, *args, **kwargs):
        captured["cmd"] = cmd

    monkeypatch.setattr(build_exe.subprocess, "check_call", _fake_check_call)

    class _NoExe:
        def is_file(self):
            return False

        def stat(self):
            raise OSError("no exe")

    monkeypatch.setattr(build_exe, "_binary_name", lambda: "rp")
    # 跳过最终 exe 检查和 env 同步，直接观察 cmd
    monkeypatch.setattr(build_exe, "_sync_env_to_dist", lambda: None)

    build_exe.main([])

    assert captured["cmd"][0] == fake_venv_py
