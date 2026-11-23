"""Read-only desktop release feed. Publishing is an operator-only filesystem action."""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

RELEASES = Path('/opt/galaxy-ai/releases')
VERSION = re.compile(r'^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$')


def release_manifest(directory=RELEASES):
    manifest = directory / 'latest.json'
    try:
        if manifest.is_symlink() or manifest.stat().st_size > 65536:
            raise ValueError('Invalid manifest')
        value = json.loads(manifest.read_text())
        version = value['version']
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError('Invalid version')
        filename = f'galaxy-ai-Setup-{version}.exe'
        package = directory / filename
        if (value['filename'] != filename or package.is_symlink() or not package.is_file()
                or type(value['size']) is not int or not 1 <= value['size'] <= 512 * 1024 * 1024
                or package.stat().st_size != value['size']
                or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])
                or not isinstance(value['notes'], str) or len(value['notes']) > 12000
                or not isinstance(value['published_at'], str) or len(value['published_at']) > 64):
            raise ValueError('Invalid release')
        return {key: value[key] for key in ('version', 'filename', 'size', 'sha256', 'notes', 'published_at')} | {
            'download_path': f'/api/v1/updates/packages/{filename}'}
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, '尚无可用更新，请稍后重试。') from None


def create_router():
    router = APIRouter(prefix='/api/v1/updates', tags=['desktop-updates'])

    @router.get('/latest')
    def latest():
        return release_manifest()

    @router.get('/packages/{filename}')
    def package(filename: str):
        # Only the currently published installer can be fetched through this API.
        value = release_manifest()
        if filename != value['filename']:
            raise HTTPException(404, '更新安装包不存在。')
        return FileResponse(RELEASES / filename, media_type='application/octet-stream', filename=filename,
                            headers={'X-Content-SHA256': value['sha256']})

    return router
