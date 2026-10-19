from pathlib import Path

import pytest
from pydantic import ValidationError

from miniagent.dashboard.config import DashboardSettings


def test_config_does_not_read_provider_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PORT=1\nDATABASE=/private/elsewhere")
    monkeypatch.setenv("PORT", "2")
    settings = DashboardSettings()
    assert settings.port == 8080
    assert settings.database == Path.home() / ".miniagent/runs.sqlite3"
    assert settings.artifacts == Path.home() / ".miniagent/artifacts"


@pytest.mark.parametrize(
    ("port", "valid"), [(0, False), (1, True), (65535, True), (65536, False)]
)
def test_port_boundaries(port, valid):
    if valid:
        assert DashboardSettings(port=port).port == port
    else:
        with pytest.raises(ValidationError):
            DashboardSettings(port=port)


def test_unknown_configuration_is_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DashboardSettings.model_validate({"provider": "unexpected"})
