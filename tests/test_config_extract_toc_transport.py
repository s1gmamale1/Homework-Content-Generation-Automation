"""EXTRACT_TOC_TRANSPORT: default cli, blank→cli, cli|api only (loud on junk)."""
import pytest

from app.config import Settings


def test_default_is_cli():
    s = Settings(_env_file=None)
    assert s.extract_toc_transport == "cli"


def test_blank_normalises_to_cli():
    s = Settings(_env_file=None, extract_toc_transport="  ")
    assert s.extract_toc_transport == "cli"


def test_api_is_accepted():
    s = Settings(_env_file=None, extract_toc_transport="api")
    assert s.extract_toc_transport == "api"


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        Settings(_env_file=None, extract_toc_transport="apii")
