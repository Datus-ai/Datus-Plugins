import pytest
from datus_tableau_plugin import cli

PROFILE = {
    "server_url": "https://tableau.example.test",
    "site_content_url": "",
    "token_name": "synthetic",
    "token_secret": "synthetic-unused",
}


@pytest.mark.parametrize("content", [None, "{invalid-json"])
def test_invalid_binding_file_is_usage_error_before_client_construction(
    tmp_path, monkeypatch, capsys, content
):
    path = tmp_path / "bindings.json"
    if content is not None:
        path.write_text(content)
    constructions = []

    def forbidden(settings):
        constructions.append(True)
        raise AssertionError("must not construct an online client")

    monkeypatch.setattr(cli, "TableauClient", forbidden)
    assert (
        cli.main(["sync", "run", "--all", "--source-bindings", str(path)], PROFILE) == 2
    )
    assert constructions == []
    assert "bindings" in capsys.readouterr().err


@pytest.mark.parametrize("use_binding_file", [False, True])
def test_sync_preloads_valid_bindings_and_omission_remains_none(
    tmp_path, monkeypatch, capsys, use_binding_file
):
    class LocalClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cli, "TableauClient", lambda settings: LocalClient())
    captured = []

    def sync(client, **kwargs):
        captured.append(kwargs["source_bindings"])
        return {"status": "ok"}

    monkeypatch.setattr(cli, "sync_sources", sync)
    argv = ["sync", "run", "--all"]
    if use_binding_file:
        path = tmp_path / "bindings.json"
        path.write_text("{}")
        argv += ["--source-bindings", str(path)]
    assert cli.main(argv, PROFILE) == 0
    assert captured == ([{}] if use_binding_file else [None])
