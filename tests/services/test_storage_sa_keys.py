import importlib
from uuid import uuid4

import app.config as config
import app.services.storage as storage


def test_sa_key_paths_honor_var_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    kid = uuid4()
    assert storage.sa_key_path(kid) == tmp_path / "sa_keys" / f"{kid}.json"
    assert storage.sa_key_active_path() == tmp_path / "sa_keys" / "active.json"
