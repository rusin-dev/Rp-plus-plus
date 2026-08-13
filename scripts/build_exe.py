"""一键编译脚本：用 PyInstaller 把项目打包为单文件 rp.exe。

用法：
    python scripts/build_exe.py            # 完整构建
    python scripts/build_exe.py --dry-run  # 仅打印 PyInstaller 命令，不执行
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "launcher.py"


def build_args() -> list[str]:
    """构造 PyInstaller 参数（可单测）。"""
    data_spec = f"{ROOT / 'src' / 'data'};src/data"
    return [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "rp",
        "--add-data",
        data_spec,
        "--paths",
        str(ROOT),
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build"),
        str(LAUNCHER),
    ]


def _command() -> list[str]:
    return [sys.executable, "-m", "PyInstaller", *build_args()]


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"]
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_exe", description="一键编译单文件 rp.exe（PyInstaller）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅打印 PyInstaller 命令，不执行"
    )
    args = parser.parse_args(argv)

    cmd = _command()
    if args.dry_run:
        print(" ".join(cmd))
        return 0

    _ensure_pyinstaller()
    subprocess.check_call(cmd)
    exe = ROOT / "dist" / "rp.exe"
    if exe.is_file():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"构建成功: {exe}（{size_mb:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
