"""Production entrypoints exist only in the remote /opt/galaxy-ai installation."""
import os
from pathlib import Path

BACKEND = Path('/opt/galaxy-ai/backend')


def production_directory():
    if Path(__file__).resolve().parents[1] != BACKEND:
        raise RuntimeError('Galaxy AI 后端仅在服务器 /opt/galaxy-ai 运行；本地检出仅用于编辑和构建，不启动后端。')
    configured = os.environ.get('XINGXU_DATA_DIR')
    if not configured or Path(configured) != BACKEND / 'private':
        raise RuntimeError('请通过服务器 galaxy-ai.service 启动，并使用 /opt/galaxy-ai/backend/private。')
    return Path(configured)
