"""一键编译脚本：用 Nuitka 把项目打包为单文件可执行程序。

用法：
    python scripts/build_exe.py            # 完整构建
    python scripts/build_exe.py --dry-run  # 仅打印 Nuitka 命令，不执行

Nuitka 不跨平台，各平台需在对应系统上编译（产物在 dist 目录下）：
    Windows   -> dist/rp.exe
    Linux     -> dist/rp
    macOS     -> dist/rp
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "launcher.py"


def build_args() -> list[str]:
    """构造 Nuitka 参数（可单测）。"""
    data_dir = ROOT / "src" / "data"
    return [
        "--onefile",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-filename=rp",
        f"--include-data-dir={data_dir}=src/data",
        f"--output-dir={ROOT / 'dist'}",
        str(LAUNCHER),
    ]


def _command() -> list[str]:
    return [sys.executable, "-m", "nuitka", *build_args()]


def _ensure_nuitka() -> None:
    try:
        import nuitka  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])


def _binary_name() -> str:
    return "rp.exe" if sys.platform == "win32" else "rp"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_exe", description="一键编译单文件 rp 可执行程序（Nuitka）"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印 Nuitka 命令，不执行")
    args = parser.parse_args(argv)

    cmd = _command()
    if args.dry_run:
        print(" ".join(cmd))
        return 0

    _ensure_nuitka()
    subprocess.check_call(cmd)
    exe = ROOT / "dist" / _binary_name()
    if exe.is_file():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"构建成功: {exe}（{size_mb:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
