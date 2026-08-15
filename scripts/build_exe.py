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
import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "launcher.py"


def build_args() -> list[str]:
    """构造 Nuitka 参数（可单测）。"""
    data_dir = ROOT / "src" / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"提示词数据目录不存在: {data_dir}")
    cmd = [
        "--onefile",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-filename=rp",
        f"--include-data-dir={data_dir}=src/data",
        f"--output-dir={ROOT / 'dist'}",
        str(LAUNCHER),
    ]
    if sys.platform == "win32":
        cmd.append("--clang")
    return cmd


def _command() -> list[str]:
    return [sys.executable, "-m", "nuitka", *build_args()]


def _build_env() -> dict[str, str]:
    """Nuitka 编译环境：把项目根目录加入模块搜索路径，确保 src 包被编译进产物。

    Nuitka 只搜索「主脚本目录 + 当前工作目录 + sys.path」，而主脚本位于
    scripts/ 下，若在其它目录执行构建，src 包将无法被找到、不会被编译进去，
    运行时会报 ModuleNotFoundError: No module named 'src.main'。
    """
    env = dict(os.environ)
    pythonpath = str(ROOT)
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath = f"{pythonpath}{os.pathsep}{existing}"
    env["PYTHONPATH"] = pythonpath
    return env


def _ensure_nuitka() -> None:
    try:
        import nuitka  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])


def _binary_name() -> str:
    return "rp.exe" if sys.platform == "win32" else "rp"


def _sync_env_to_dist() -> None:
    """把根目录 .env 同步到产物旁：不存在则复制，已存在则提示（避免覆盖手工修改）。"""
    src = ROOT / ".env"
    if not src.is_file():
        return
    import shutil

    dst = ROOT / "dist" / ".env"
    if dst.exists():
        print(f"提示: {dst} 已存在，未覆盖；如需更新请手动从 {src} 复制")
        return
    shutil.copy2(src, dst)
    print(f"已复制环境配置到产物旁: {dst}")


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
    subprocess.check_call(cmd, cwd=ROOT, env=_build_env())
    exe = ROOT / "dist" / _binary_name()
    if exe.is_file():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"构建成功: {exe}（{size_mb:.1f} MB）")
    _sync_env_to_dist()
    return 0


if __name__ == "__main__":
    sys.exit(main())
