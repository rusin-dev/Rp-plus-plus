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
