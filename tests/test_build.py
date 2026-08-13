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
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.chdir(tmp_path)
    assert config_module._resolve_root() == tmp_path
    assert config_module._resolve_data_dir() == tmp_path / "bundle" / "src" / "data"


def test_config_attrs_stable_in_non_frozen_env():
    assert Config.ROOT_DIR == config_module._resolve_root()
    assert Config.DATA_DIR == config_module._resolve_data_dir()


# ---------- build_exe.py 参数构造 ----------


def test_build_args_for_nuitka():
    from scripts.build_exe import build_args

    args = build_args()
    assert "--onefile" in args
    assert "--standalone" in args
    assert "--assume-yes-for-downloads" in args
    assert any(a == "--output-filename=rp" for a in args)
    data_spec = next(a for a in args if a.startswith("--include-data-dir="))
    assert "src/data" in data_spec
    assert Path(args[args.index("--output-dir") + 1]).name == "dist"
    assert args[-1].endswith("launcher.py")


def test_build_args_data_spec_targets_src_data():
    from scripts.build_exe import build_args

    data_spec = next(
        a for a in build_args() if a.startswith("--include-data-dir=")
    ).removeprefix("--include-data-dir=")
    source, target = data_spec.split("=")
    assert Path(source).is_dir()
    assert target == "src/data"


def test_build_main_dry_run():
    from scripts.build_exe import main

    assert main(["--dry-run"]) == 0
