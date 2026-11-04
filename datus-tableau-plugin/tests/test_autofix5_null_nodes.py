import json
import pytest
from datus_tableau_plugin.native import parse_flow
from datus_tableau_plugin.prep import native_flow
from datus_tableau_plugin.plan import main


@pytest.mark.parametrize("nodes", [None, 1, True, "invalid-container"])
def test_native_flow_rejects_invalid_node_collections_as_value_error(nodes):
    with pytest.raises(ValueError, match="nodes"):
        native_flow({"nodes": nodes})
    with pytest.raises(ValueError, match="nodes"):
        parse_flow(json.dumps({"nodes": nodes}).encode(), flow_id="fixture")


def test_null_nodes_cli_returns_controlled_usage_error_without_output(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "flow.tfl"
    path.write_text('{"nodes":null}')
    with pytest.raises(SystemExit) as exc:
        main(["--flow", str(path), "--output", "plan"])
    assert exc.value.code == 2
    assert "nodes" in capsys.readouterr().err
    assert not (tmp_path / "plan").exists()


@pytest.mark.parametrize("nodes", [{}, []])
def test_empty_valid_node_containers_remain_accepted(nodes):
    assert native_flow({"nodes": nodes}) is False
    assert (
        len(
            parse_flow(json.dumps({"nodes": nodes}).encode(), flow_id="fixture")[
                "assets"
            ]
        )
        == 1
    )
