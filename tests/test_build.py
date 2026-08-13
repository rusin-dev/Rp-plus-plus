import sys
from pathlib import Path

from src import config as config_module
from src.config import Config

# ---------- config.py 冻结路径适配 ----------


def test_non_frozen_paths_match_source_tree():
    root = config_module._resolve_root()
    assert (root / "src" / "config.py").is_file()
    assert config_module._resolve_data_dir() == root / "src" / "data"


def test_frozen_paths_use_cwd_and_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "_FROZEN", True)
    monkeypatch.setattr(
        sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False
    )
    monkeypatch.chdir(tmp_path)
    assert config_module._resolve_root() == tmp_path
    assert config_module._resolve_data_dir() == tmp_path / "bundle" / "src" / "data"


def test_config_attrs_stable_in_non_frozen_env():
    assert Config.ROOT_DIR == config_module._resolve_root()
    assert Config.DATA_DIR == config_module._resolve_data_dir()


# ---------- build_exe.py 参数构造 ----------


def test_build_args_for_pyinstaller():
    from scripts.build_exe import build_args

    args = build_args()
    assert "--onefile" in args
    assert "--console" in args
    assert args[args.index("--name") + 1] == "rp"
    data_spec = args[args.index("--add-data") + 1]
    assert "src/data" in data_spec
    assert Path(args[args.index("--paths") + 1]).name == "rp--your-programming-co-pilot"
    assert args[-1].endswith("launcher.py")


def test_build_main_dry_run():
    from scripts.build_exe import main

    assert main(["--dry-run"]) == 0
