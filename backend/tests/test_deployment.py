from pathlib import Path
import pytest
from xingxu import deployment
from xingxu.app import create_app


def test_local_production_entrypoint_does_not_create_storage_or_bind_network(monkeypatch, tmp_path):
    monkeypatch.setattr(deployment, '__file__', str(tmp_path/'checkout/backend/xingxu/deployment.py'))
    monkeypatch.setenv('XINGXU_DATA_DIR', str(tmp_path/'private'))
    with pytest.raises(RuntimeError, match='仅在服务器'):
        create_app()
    assert not (tmp_path/'private').exists()


def test_server_installation_requires_explicit_server_data_directory(monkeypatch):
    monkeypatch.setattr(deployment, '__file__', '/opt/galaxy-ai/backend/xingxu/deployment.py')
    monkeypatch.delenv('XINGXU_DATA_DIR', raising=False)
    with pytest.raises(RuntimeError, match='galaxy-ai.service'):
        deployment.production_directory()
    monkeypatch.setenv('XINGXU_DATA_DIR', '/home/chiaki/local-private')
    with pytest.raises(RuntimeError):deployment.production_directory()
    monkeypatch.setenv('XINGXU_DATA_DIR', '/opt/galaxy-ai/backend/private')
    assert deployment.production_directory() == Path('/opt/galaxy-ai/backend/private')
