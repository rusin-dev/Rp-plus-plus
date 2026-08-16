"""一键编译脚本：根据平台选择打包工具。

- Windows: PyInstaller
- macOS / Linux: Nuitka

用法：
    python scripts/build_exe.py            # 完整构建
    python scripts/build_exe.py --dry-run  # 仅打印命令，不执行

各平台需在对应系统上编译（产物在 dist 目录下）：
    Windows   -> dist/rp.exe   (PyInstaller --onefile)
    Linux     -> dist/rp       (Nuitka --onefile)
    macOS     -> dist/rp       (Nuitka --onefile)
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
    """构造打包参数（可单测）。"""
    data_dir = ROOT / "src" / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"提示词数据目录不存在: {data_dir}")

    if sys.platform == "win32":
        sep = ";"
        return [
            "--onefile",
            "--name=rp",
            f"--add-data={data_dir}{sep}src/data",
            f"--distpath={ROOT / 'dist'}",
            f"--paths={ROOT}",
            "--clean",
            "--noconfirm",
            str(LAUNCHER),
        ]

    cmd = [
        "--onefile",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-filename=rp",
        f"--include-data-dir={data_dir}=src/data",
        f"--output-dir={ROOT / 'dist'}",
        "--static-libpython=no",
        "--lto=yes",
        str(LAUNCHER),
    ]
    return cmd


def _command() -> list[str]:
    if sys.platform == "win32":
        return [sys.executable, "-m", "PyInstaller", *build_args()]
    return [sys.executable, "-m", "nuitka", *build_args()]


def _build_env() -> dict[str, str]:
    """打包环境：把项目根目录加入模块搜索路径，确保 src 包能被分析/编译进产物。

    PyInstaller / Nuitka 都依赖主脚本所在目录 + 当前工作目录 + sys.path
    来发现依赖。主脚本位于 scripts/ 下，若在其它目录执行构建，
    src 包将无法被找到、不会被打包，运行时会报 ModuleNotFoundError。
    """
    env = dict(os.environ)
    pythonpath = str(ROOT)
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath = f"{pythonpath}{os.pathsep}{existing}"
    env["PYTHONPATH"] = pythonpath
    return env


def _ensure_builder() -> None:
    """按平台安装对应打包工具。"""
    if sys.platform == "win32":
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        return

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
        prog="build_exe",
        description="一键编译单文件 rp 可执行程序（Windows 走 PyInstaller，其余平台走 Nuitka）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印命令，不执行")
    args = parser.parse_args(argv)

    cmd = _command()
    if args.dry_run:
        print(" ".join(cmd))
        return 0

    _ensure_builder()
    subprocess.check_call(cmd, cwd=ROOT, env=_build_env())
    exe = ROOT / "dist" / _binary_name()
    if exe.is_file():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"构建成功: {exe}（{size_mb:.1f} MB）")
    _sync_env_to_dist()
    return 0


if __name__ == "__main__":
    sys.exit(main())
