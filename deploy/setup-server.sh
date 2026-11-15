#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$project_dir" != /opt/galaxy-ai ]]; then
  echo '仅在服务器 /opt/galaxy-ai 执行；本地没有安装或启动后端的流程。' >&2
  exit 1
fi
# Dependency files contain no credentials and must remain readable by galaxy-ai.
# Runtime secrets are created under the service's UMask=0077.
umask 022
python3 -m venv "$project_dir/backend/.venv"
"$project_dir/backend/.venv/bin/python" -m pip install -r "$project_dir/backend/requirements.txt" -e "$project_dir/galaxy-cli"
python3 -m venv "$project_dir/galaxy-cli/.venv"
"$project_dir/galaxy-cli/.venv/bin/python" -m pip install -e "$project_dir/galaxy-cli"
echo '服务器运行环境已准备；通过 galaxy-ai.service 启动，不创建用户 HOME 下的 CLI 链接。'
