import pytest

import previews


@pytest.fixture(autouse=True)
def _previews_out_of_the_repo(monkeypatch, tmp_path):
    """run() writes a preview page per film. Without this, any test that lets
    run() reach the end would drop pages for fake films into the real docs/p/,
    which the next commit would then publish."""
    monkeypatch.setattr(previews, "PREVIEW_DIR", str(tmp_path / "p"))
