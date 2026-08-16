#!/usr/bin/env bash
# 一键清理所有 Python 编译产物（__pycache__ 目录与 .pyc/.pyo 文件）。
# 用法：bash scripts/clean_pyc.sh   或   直接执行 scripts/clean_pyc.sh
set -u

cd "$(dirname "$0")/.." || exit 1

count_pyc=0
count_dir=0

while IFS= read -r -d '' path; do
    rm -rf "$path"
    count_dir=$((count_dir + 1))
done < <(find . -path ./.venv -prune -o -type d -name "__pycache__" -print0)

while IFS= read -r -d '' path; do
    rm -f "$path"
    count_pyc=$((count_pyc + 1))
done < <(find . -path ./.venv -prune -o -type f \( -name "*.pyc" -o -name "*.pyo" \) -print0)

echo "清理完成：删除 ${count_dir} 个 __pycache__ 目录，${count_pyc} 个字节码文件"