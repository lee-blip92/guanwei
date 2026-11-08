import importlib
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

@pytest.fixture
def client(tmp_path, monkeypatch):
    from insight import config
    from insight.jobs import Manager
    module = importlib.import_module('insight.app')
    monkeypatch.setattr(config, 'DATA', tmp_path)
    monkeypatch.setattr(module, 'manager', Manager())
    with TestClient(module.app) as c:
        yield c
