from xml.etree import ElementTree as ET
from test_calculation_dependencies import assess, chart_statuses, parse, workbook


def test_qualified_calculation_adds_deduplicated_dataset_edge_and_blocks_external_etl():
    root = workbook(
        {
            "sales": {"Amount": None, "Cross": "[other].[Amount] + [other].[Amount]"},
            "other": {"Amount": None},
        },
        [
            ("consumer", ["sales"], "[sales].[Cross]", None),
            ("safe", ["sales"], "[sales].[Amount]", None),
            ("qualified", ["sales"], "[other].[Amount]", None),
        ],
    )
    other = root.find('./datasources/datasource[@name="other"]')
    ET.SubElement(
        ET.SubElement(other, "connection"),
        "relation",
        {"type": "table", "table": "warehouse.other_source"},
    )
    result = parse(root)
    assert chart_statuses(result) == {"consumer": "ok", "safe": "ok", "qualified": "ok"}
    for name in ["consumer", "qualified"]:
        edges = [
            e
            for e in result["dependencies"]
            if e
            == {
                "from": "chart:calculations:" + name,
                "to": "dataset:calculations:other",
                "relation": "depends_on",
            }
        ]
        assert len(edges) == 1
    assert not any(
        e["from"] == "chart:calculations:safe"
        and e["to"] == "dataset:calculations:other"
        for e in result["dependencies"]
    )
    report = assess(result)
    assert report["summary"] == {"total": 3, "ready": 1, "blocked": 2}
    assert len(report["charts"]) == 3
    charts = {chart["name"]: chart for chart in report["charts"]}
    assert set(charts) == {"safe", "consumer", "qualified"}
    assert charts["safe"]["status"] == "ready_for_metric_validation"
    assert len(charts["safe"]["tool_calls"]) == 1
    assert charts["consumer"]["status"] == "blocked"
    assert any(
        "external_etl_unresolved" in reason for reason in charts["consumer"]["reasons"]
    )
    assert charts["consumer"]["tool_calls"] == []
    assert charts["qualified"]["status"] == "blocked"
    assert any(
        "external_etl_unresolved" in reason for reason in charts["qualified"]["reasons"]
    )
    assert charts["qualified"]["tool_calls"] == []


def test_sparse_cross_source_fields_keep_dataset_lineage_and_external_etl_blockers():
    root = workbook(
        {"sales": {"Amount": None, "Cross": "[other].[Amount] + 1"}, "other": {}},
        [
            ("consumer", ["sales"], "[sales].[Cross]", None),
            ("safe", ["sales"], "[sales].[Amount]", None),
            ("qualified", ["sales"], "[other].[Amount]", None),
        ],
    )
    other = root.find('./datasources/datasource[@name="other"]')
    assert other.findall("column") == []
    ET.SubElement(
        ET.SubElement(other, "connection"),
        "relation",
        {"type": "table", "table": "warehouse.other_source"},
    )
    result = parse(root)
    assert chart_statuses(result) == {"consumer": "ok", "safe": "ok", "qualified": "ok"}
    assert [
        edge
        for edge in result["dependencies"]
        if edge["to"] == "dataset:calculations:other"
        and edge["relation"] == "depends_on"
    ] == [
        {
            "from": "chart:calculations:consumer",
            "to": "dataset:calculations:other",
            "relation": "depends_on",
        },
        {
            "from": "chart:calculations:qualified",
            "to": "dataset:calculations:other",
            "relation": "depends_on",
        },
    ]
    external = [
        asset for asset in result["assets"] if asset["kind"] == "external_source"
    ]
    assert len(external) == 1
    assert external[0]["definition"]["table"] == "warehouse.other_source"
    expected_reasons = [f"{external[0]['id']}: external_etl_unresolved"]
    report = assess(result)
    assert report["summary"] == {"total": 3, "ready": 1, "blocked": 2}
    assert len(report["charts"]) == 3
    charts = {chart["name"]: chart for chart in report["charts"]}
    assert set(charts) == {"consumer", "safe", "qualified"}
    assert charts["consumer"]["status"] == "blocked"
    assert charts["consumer"]["reasons"] == expected_reasons
    assert charts["consumer"]["tool_calls"] == []
    assert charts["qualified"]["status"] == "blocked"
    assert charts["qualified"]["reasons"] == expected_reasons
    assert charts["qualified"]["tool_calls"] == []
    assert charts["safe"]["status"] == "ready_for_metric_validation"
    assert charts["safe"]["reasons"] == []
    assert len(charts["safe"]["tool_calls"]) == 1
