"""一键编译脚本：根据平台选择打包工具。

- Windows: PyInstaller
- macOS / Linux: Nuitka

为避免把本机 pip 全量库打进产物（参考 Issue #20），构建过程在临时干净 venv 中进行，
仅安装 requirements.txt 与对应打包工具。

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
import venv
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "launcher.py"
BUILD_DIR = ROOT / "build"
BUILD_VENV = BUILD_DIR / ".venv-build"

# 防御性排除：常见重型/无关库。即便在干净 venv 中通常不会被静态分析触及，
# 但加上显式排除更稳妥，也能让构建意图在命令行上可见。
EXCLUDED_MODULES = (
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "tf_keras",
    "keras",
    "jax",
    "flax",
    "transformers",
    "datasets",
    "accelerate",
    "scipy",
    "pandas",
    "sklearn",
    "scikit-learn",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
)


def build_args() -> list[str]:
    """构造打包参数（可单测）。"""
    data_dir = ROOT / "src" / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"提示词数据目录不存在: {data_dir}")

    if sys.platform == "win32":
        sep = ";"
        args = [
            "--onefile",
            "--name=rp",
            f"--add-data={data_dir}{sep}src/data",
            f"--distpath={ROOT / 'dist'}",
            f"--paths={ROOT}",
            "--clean",
            "--noconfirm",
        ]
        for mod in EXCLUDED_MODULES:
            args.append(f"--exclude-module={mod}")
        args.append(str(LAUNCHER))
        return args

    cmd = [
        "--onefile",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-filename=rp",
        f"--include-data-dir={data_dir}=src/data",
        f"--output-dir={ROOT / 'dist'}",
        "--static-libpython=no",
        "--lto=yes",
    ]
    for mod in EXCLUDED_MODULES:
        cmd.append(f"--nofollow-import-to={mod}")
    cmd.append(str(LAUNCHER))
    return cmd


def _venv_python() -> str:
    """构建用 venv 内 Python 解释器的路径。"""
    if sys.platform == "win32":
        return str(BUILD_VENV / "Scripts" / "python.exe")
    return str(BUILD_VENV / "bin" / "python")


def _command() -> list[str]:
    """构造完整打包命令（首项 Python 路径由 main() 在执行时替换为 venv 内的解释器）。"""
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


def _builder_package() -> str:
    """当前平台对应的打包工具包名。"""
    return "pyinstaller" if sys.platform == "win32" else "nuitka"


def _ensure_clean_venv() -> None:
    """创建/复用干净构建 venv，仅安装 requirements.txt + 打包工具。

    解决 Issue #20：本机 pip 全量库（如 torch、jupyter 等）会被 PyInstaller/Nuitka
    当作依赖一起打进产物，导致文件条目数超过 32 位无符号整数上限、产物极度臃肿。
    使用独立 venv 后，打包工具只看见项目真实依赖。
    """
    BUILD_DIR.mkdir(exist_ok=True)
    py = _venv_python()
    req = ROOT / "requirements.txt"
    builder_pkg = _builder_package()

    # 复用：venv 存在、stamp 不早于 requirements.txt
    marker = BUILD_VENV / ".stamp"
    if Path(py).is_file() and marker.is_file() and marker.stat().st_mtime >= req.stat().st_mtime:
        print(f"复用构建 venv: {BUILD_VENV}")
        return

    print(f"创建构建用 venv: {BUILD_VENV}")
    env_builder = venv.EnvBuilder(
        system_site_packages=False,
        clear=True,
        with_pip=True,
    )
    env_builder.create(str(BUILD_VENV))

    print("升级 pip 并安装运行时依赖（requirements.txt）...")
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-r", str(req)])
    print(f"安装打包工具: {builder_pkg}")
    subprocess.check_call([py, "-m", "pip", "install", builder_pkg])

    marker.touch()
    print(f"构建 venv 就绪: {py}")


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
        # dry-run 模式下若 venv 已存在，复用其 Python 路径让命令更真实
        if Path(_venv_python()).is_file():
            cmd[0] = _venv_python()
        print(" ".join(cmd))
        return 0

    _ensure_clean_venv()
    # 用干净 venv 中的 Python 执行打包，避免本机 pip 库被打包
    cmd[0] = _venv_python()

    subprocess.check_call(cmd, cwd=ROOT, env=_build_env())
    exe = ROOT / "dist" / _binary_name()
    if exe.is_file():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"构建成功: {exe}（{size_mb:.1f} MB）")
    _sync_env_to_dist()
    return 0


if __name__ == "__main__":
    sys.exit(main())
