#!/usr/bin/env python3
"""Run on the server: publish-desktop.py /path/galaxy-ai-Setup-X.Y.Z.exe --notes-file notes.txt"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def publish(source, directory, notes):
    match = re.fullmatch(r'galaxy-ai-Setup-((?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5}))\.exe', source.name)
    if not match or not source.is_file() or source.is_symlink():
        raise ValueError('安装包须命名为 galaxy-ai-Setup-X.Y.Z.exe')
    if not 1 <= source.stat().st_size <= 512 * 1024 * 1024 or len(notes) > 12000:
        raise ValueError('安装包或说明大小无效')
    with source.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('安装包不是 Windows EXE')
    directory.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    destination = directory / source.name
    if destination.exists():
        with destination.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('同一版本已有不同安装包，请递增版本号')
    elif source.resolve() != destination.resolve():
        fd, temporary = tempfile.mkstemp(prefix='.package-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as output, source.open('rb') as stream:
                shutil.copyfileobj(stream, output)
                output.flush(); os.fsync(output.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    # Uploads may preserve operator-only mode, including when source == destination.
    # Installers are public downloads; the service must read an already present package too.
    destination.chmod(0o644)
    value = {'version': match[1], 'filename': source.name, 'size': destination.stat().st_size,
             'sha256': digest, 'notes': notes.strip(), 'published_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    fd, temporary = tempfile.mkstemp(prefix='.manifest-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush(); os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, directory / 'latest.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('--notes-file', required=True, type=Path)
    parser.add_argument('--directory', type=Path, default=Path('/opt/galaxy-ai/releases'))
    args = parser.parse_args()
    print(json.dumps(publish(args.package, args.directory, args.notes_file.read_text()), ensure_ascii=False))
