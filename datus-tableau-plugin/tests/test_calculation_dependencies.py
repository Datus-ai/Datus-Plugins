from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from datus_tableau_plugin.native import parse_workbook
from datus_tableau_plugin.rebuild import assess_rebuild


def column(parent, name, formula=None, **attributes):
    element = ET.SubElement(parent, "column", name=f"[{name}]", **attributes)
    if formula is not None:
        ET.SubElement(element, "calculation", {"class": "tableau", "formula": formula})
    return element


def workbook(sources, sheets):
    root = ET.Element("workbook")
    datasources = ET.SubElement(root, "datasources")
    for name, definitions in sources.items():
        source = ET.SubElement(datasources, "datasource", name=name)
        for field, formula in definitions.items():
            column(source, field, formula)
    worksheets = ET.SubElement(root, "worksheets")
    for name, source_names, binding, local in sheets:
        worksheet = ET.SubElement(worksheets, "worksheet", name=name)
        table = ET.SubElement(worksheet, "table")
        view = ET.SubElement(table, "view")
        refs = ET.SubElement(view, "datasources")
        for source in source_names:
            ET.SubElement(refs, "datasource", name=source)
        ET.SubElement(table, "rows").text = binding
        if local:
            view.append(ET.fromstring(local))
    return root


def parse(root):
    return parse_workbook(ET.tostring(root), workbook_id="calculations")


def chart_statuses(result):
    return {
        asset["name"]: asset["status"]
        for asset in result["assets"]
        if asset["kind"] == "chart"
    }


def assess(result):
    metric = {"path": ["Sales"], "name": "revenue"}
    revision = "b" * 64
    return assess_rebuild(
        result,
        source_manifest_sha256="a" * 64,
        metric_catalog=[
            {
                "metric": metric,
                "datasource": "warehouse",
                "model_revision": revision,
                "dimensions": [],
            }
        ],
        bindings={
            "schema_version": 1,
            "source_manifest_sha256": "a" * 64,
            "charts": [
                {
                    "chart_id": asset["id"],
                    "differences": [],
                    "queries": [
                        {
                            "name": f"metric_{index}",
                            "datasource": "warehouse",
                            "model_revision": revision,
                            "goal": "Compare revenue",
                            "hypothesis": "Revenue changes over time",
                            "query": {"metric": metric},
                            "params": [],
                            "sample_params": {},
                        }
                    ],
                }
                for index, asset in enumerate(result["assets"])
                if asset["kind"] == "chart"
            ],
        },
    )


def test_direct_and_multihop_lod_block_only_consuming_charts_through_rebuild():
    result = parse(
        workbook(
            {
                "sales": {
                    "Amount": None,
                    "A": "{FIXED : SUM([Amount])}",
                    "LOD": "{ FIXED : SUM([Amount]) }",
                    "Scaled": "[LOD] * 2",
                    "Rounded": "ROUND([Scaled])",
                    "Share": "SUM([Amount]) / TOTAL(SUM([Amount]))",
                    "Previous": "PREVIOUS_VALUE(SUM([Amount]))",
                }
            },
            [
                (name, ["sales"], f"[sales].[sum:{field}:qk]", None)
                for name, field in [
                    ("direct", "LOD"),
                    ("indirect", "Scaled"),
                    ("multihop", "Rounded"),
                    ("total", "Share"),
                    ("previous", "Previous"),
                    ("ordinary", "Amount"),
                ]
            ],
        )
    )
    assert chart_statuses(result) == {
        "direct": "unsupported",
        "indirect": "unsupported",
        "multihop": "unsupported",
        "total": "unsupported",
        "previous": "unsupported",
        "ordinary": "ok",
    }
    report = assess(result)
    assert report["summary"] == {"total": 6, "ready": 1, "blocked": 5}
    for chart in report["charts"]:
        if chart["name"] == "ordinary":
            assert chart["status"] == "ready_for_metric_validation"
            assert len(chart["tool_calls"]) == 1
        else:
            assert chart["status"] == "blocked"
            assert any("tableau_calculation" in reason for reason in chart["reasons"])
            assert chart["tool_calls"] == []


def test_worksheet_local_calculations_instances_and_datasource_scope():
    local = """<datasource-dependencies datasource="sales">
      <column name="[Local]"><calculation class="tableau" formula="[Scaled] + 1"/></column>
      <column name="[Unused]"><calculation class="tableau" formula="TOTAL(SUM([Amount]))"/></column>
      <column-instance name="[usr:Local:qk]" column="[Local]" derivation="User"/>
    </datasource-dependencies>"""
    result = parse(
        workbook(
            {
                "sales": {
                    "Amount": None,
                    "Shared": "{FIXED : SUM([Amount])}",
                    "Scaled": "[Shared] * 2",
                },
                "other": {
                    "Amount": None,
                    "Shared": "SUM([Amount])",
                    "Scaled": "[Shared] * 2",
                },
            },
            [
                ("local", ["sales", "other"], "[sales].[usr:Local:qk]", local),
                ("safe", ["sales", "other"], "[other].[Scaled]", local),
                ("unused", ["sales"], "[sales].[Amount]", local),
            ],
        )
    )
    assert chart_statuses(result) == {
        "local": "unsupported",
        "safe": "ok",
        "unused": "ok",
    }
    assert assess(result)["summary"] == {"total": 3, "ready": 2, "blocked": 1}


@pytest.mark.parametrize(
    "formula",
    [
        "TOTAL(SUM([Amount]))",
        "PREVIOUS_VALUE(0)",
        "WINDOW_SUM(SUM([Amount]))",
        "RUNNING_SUM(SUM([Amount]))",
        "RANK_DENSE(SUM([Amount]))",
        "LOOKUP(SUM([Amount]), -1)",
        "INDEX()",
        "SIZE()",
        "FIRST()",
        "LAST()",
        "MODEL_PERCENTILE(SUM([Amount]), SUM([Amount]))",
        "MODEL_QUANTILE(0.5, SUM([Amount]), SUM([Amount]))",
        'SCRIPT_REAL("return .arg1", SUM([Amount]))',
        'MODEL_EXTENSION_STRING("model", "input", SUM([Amount]))',
    ],
)
def test_documented_table_calculations_are_not_ready(formula):
    result = parse(
        workbook(
            {"sales": {"Amount": None, "Used": formula}},
            [("used", ["sales"], "[sales].[Used]", None)],
        )
    )
    assert chart_statuses(result) == {"used": "unsupported"}
    assert any(
        issue["code"] == "tableau_calculation"
        and issue["asset_id"].startswith("chart:")
        for issue in result["coverage"]
    )


@pytest.mark.parametrize(
    "formula",
    [
        "SUM([Amount]) / COUNT([Amount])",
        "IF [Amount] > 0 THEN \"TOTAL([LOD])\" ELSE 'PREVIOUS_VALUE(0)' END",
        "SUM([Amount]) // TOTAL([LOD])\n + 1 /* { FIXED : [LOD] } */",
        "IF #2026-01-01# = #2026-01-01# THEN SUM([Amount]) END",
        "'it\\'s [LOD] TOTAL(0)'",
        '"a ""[LOD]"" PREVIOUS_VALUE(0)"',
        "[TOTAL(SUM)]",
    ],
)
def test_literals_comments_and_basic_aggregates_do_not_infer_calculation_use(formula):
    root = workbook(
        {
            "sales": {
                "Amount": None,
                "LOD": "{FIXED : SUM([Amount])}",
                "TOTAL(SUM)": None,
                "SUM": "TOTAL(1)",
                "IF": "TOTAL(1)",
                "Used": formula,
            }
        },
        [("[LOD] TOTAL(0)", ["sales"], "[sales].[Used]", None)],
    )
    ET.SubElement(
        root.find(".//worksheet/table"),
        "mark",
        {"caption": "[sales].[LOD]", "name": "[LOD]"},
    )
    assert chart_statuses(parse(root)) == {"[LOD] TOTAL(0)": "ok"}


def test_known_bare_field_dependencies_and_qualified_cross_source_formula():
    result = parse(
        workbook(
            {
                "sales": {
                    "Amount": None,
                    "LOD": "{FIXED : SUM([Amount])}",
                    "Bare": "LOD * 2",
                    "Cross": "[other].[LOD] * 2",
                },
                "other": {"Amount": None, "LOD": "SUM([Amount])"},
            },
            [
                ("bare", ["sales"], "[sales].[Bare]", None),
                ("cross", ["sales", "other"], "[sales].[Cross]", None),
            ],
        )
    )
    assert chart_statuses(result) == {"bare": "unsupported", "cross": "ok"}


@pytest.mark.parametrize(
    ("definitions", "binding", "local", "code"),
    [
        ({"A": "[B]", "B": "[A]"}, "[sales].[A]", None, "calculation_dependency_cycle"),
        (
            {"A": "[Calculation_missing] + 1"},
            "[sales].[A]",
            None,
            "calculation_dependency_unresolved",
        ),
        (
            {"A": "SUM([Amount])"},
            "[sales].[A]",
            '<datasource-dependencies datasource="sales"><column name="[A]"><calculation formula="TOTAL(1)" class="tableau"/></column></datasource-dependencies>',
            "calculation_dependency_unresolved",
        ),
        (
            {"Amount": None},
            "[sales].[usr:Missing:qk]",
            '<datasource-dependencies datasource="sales"><column-instance name="[usr:Missing:qk]" column="[Missing]"/></datasource-dependencies>',
            "calculation_dependency_unresolved",
        ),
        (
            {"Amount": None},
            "[sales].[Incomplete]",
            '<datasource-dependencies datasource="sales"><column name="[Incomplete]"><calculation class="tableau"/></column></datasource-dependencies>',
            "calculation_dependency_unresolved",
        ),
    ],
)
def test_uncertain_calculation_graphs_block_only_the_consumer(
    definitions, binding, local, code
):
    result = parse(
        workbook(
            {"sales": {"Amount": None, **definitions}},
            [
                ("uncertain", ["sales"], binding, local),
                ("safe", ["sales"], "[sales].[Amount]", local),
            ],
        )
    )
    assert chart_statuses(result) == {"uncertain": "unsupported", "safe": "ok"}
    assert any(
        issue["code"] == code and issue["asset_id"] == "chart:calculations:uncertain"
        for issue in result["coverage"]
    )
    assert assess(result)["summary"] == {"total": 2, "ready": 1, "blocked": 1}


def test_ambiguous_unqualified_binding_is_not_assigned_to_either_datasource():
    result = parse(
        workbook(
            {"sales": {"Used": "TOTAL(1)"}, "other": {"Used": "SUM([Amount])"}},
            [
                ("ambiguous", ["sales", "other"], "[Used]", None),
                ("qualified", ["sales", "other"], "[other].[Used]", None),
            ],
        )
    )
    assert chart_statuses(result) == {"ambiguous": "unsupported", "qualified": "ok"}
    assert any(
        issue["code"] == "calculation_dependency_unresolved"
        for issue in result["coverage"]
    )


def test_deep_shared_dependency_chain_uses_no_python_recursion():
    definitions = {"Amount": None, "C0": "{FIXED : SUM([Amount])}"}
    definitions.update(
        {f"C{index}": f"[C{index - 1}] + [C{index - 1}]" for index in range(1, 1200)}
    )
    result = parse(
        workbook(
            {"sales": definitions},
            [
                ("deep", ["sales"], "[sales].[C1199]", None),
                ("safe", ["sales"], "[sales].[Amount]", None),
            ],
        )
    )
    assert chart_statuses(result) == {"deep": "unsupported", "safe": "ok"}
    assert assess(result)["summary"] == {"total": 2, "ready": 1, "blocked": 1}


def test_sparse_physical_columns_and_parameters_remain_compatible():
    result = parse(
        workbook(
            {
                "sales": {"Used": "SUM([revenue]) + [Parameters].[Rate]"},
                "Parameters": {"Rate": None},
            },
            [("sparse", ["sales"], "[sales].[Used]", None)],
        )
    )
    assert chart_statuses(result) == {"sparse": "ok"}


def test_unused_local_table_calculation_does_not_hide_active_addressing_blockers():
    local = '<datasource-dependencies datasource="sales"><column name="[Unused]"><calculation class="table" formula="SUM([Amount])"/></column></datasource-dependencies>'
    root = workbook(
        {"sales": {"Amount": None}},
        [
            ("unused", ["sales"], "[sales].[Amount]", local),
            ("addressing", ["sales"], "[sales].[Amount]", local),
            ("used", ["sales"], "[sales].[Unused]", local),
        ],
    )
    ET.SubElement(
        root.find("./worksheets/worksheet[@name='addressing']/table"),
        "table-calc",
        {"ordering-type": "Rows"},
    )
    result = parse(root)
    assert chart_statuses(result) == {
        "unused": "ok",
        "addressing": "unsupported",
        "used": "unsupported",
    }
    assert any(
        issue["code"] == "table_calculation"
        and issue["asset_id"] == "chart:calculations:addressing"
        for issue in result["coverage"]
    )


def test_active_inline_calculation_follows_known_bare_fields():
    root = workbook(
        {"sales": {"LOD": "{FIXED : SUM([Amount])}"}}, [("inline", ["sales"], "", None)]
    )
    ET.SubElement(
        root.find(".//worksheet/table"),
        "calculation",
        {"class": "tableau", "formula": "LOD * 2"},
    )
    assert chart_statuses(parse(root)) == {"inline": "unsupported"}


@pytest.mark.parametrize(
    "literal",
    [
        "1e3",
        "1.2E6",
        "1.e3",
        ".5e+2",
        "1e-3",
        "1.E+6",
        ".5E-2",
        "-1e3",
        "+1.2E6",
        "-1.e-3",
    ],
)
def test_numeric_literals_do_not_reference_unused_exponent_named_fields(literal):
    result = parse(
        workbook(
            {
                "sales": {
                    "Amount": None,
                    "e3": "TOTAL(SUM([Amount]))",
                    "E6": "PREVIOUS_VALUE(0)",
                    "e": "TOTAL(1)",
                    "E": "TOTAL(1)",
                    "Numeric": f"{literal} * SUM([Amount])",
                    "ActualField": "e3 * SUM([Amount])",
                }
            },
            [
                ("numeric", ["sales"], "[sales].[Numeric]", None),
                ("actual_field", ["sales"], "[sales].[ActualField]", None),
            ],
        )
    )
    assert chart_statuses(result) == {"numeric": "ok", "actual_field": "unsupported"}
    report = assess(result)
    assert report["summary"] == {"total": 2, "ready": 1, "blocked": 1}
    charts = {chart["name"]: chart for chart in report["charts"]}
    assert charts["numeric"]["status"] == "ready_for_metric_validation"
    assert len(charts["numeric"]["tool_calls"]) == 1
    assert charts["actual_field"]["status"] == "blocked"
    assert charts["actual_field"]["tool_calls"] == []
    assert any(
        "tableau_calculation" in reason for reason in charts["actual_field"]["reasons"]
    )
