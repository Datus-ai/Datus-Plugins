from xml.etree import ElementTree as ET
import pytest
from test_calculation_dependencies import assess, chart_statuses, parse, workbook


@pytest.mark.parametrize("placeholder", ["<[sales].[LOD]>", "<[sales].[sum:LOD:qk]>"])
def test_tooltip_placeholder_lod_blocks_chart_and_rebuild_only_for_consumers(
    placeholder,
):
    root = workbook(
        {"sales": {"Amount": None, "LOD": "{FIXED : SUM([Amount])}"}},
        [
            ("tooltip", ["sales"], "[sales].[Amount]", None),
            ("literal", ["sales"], "[sales].[Amount]", None),
            ("outside", ["sales"], "[sales].[Amount]", None),
        ],
    )
    sheets = {s.get("name"): s for s in root.findall("./worksheets/worksheet")}
    tooltip = ET.SubElement(sheets["tooltip"].find("table"), "customized-tooltip")
    ET.SubElement(ET.SubElement(tooltip, "formatted-text"), "run").text = (
        "Value: " + placeholder
    )
    literal = ET.SubElement(sheets["literal"].find("table"), "customized-tooltip")
    ET.SubElement(
        ET.SubElement(literal, "formatted-text"), "run"
    ).text = "ordinary [sales].[LOD] [LOD] <plain caption>"
    ET.SubElement(sheets["outside"].find("table"), "run").text = placeholder
    result = parse(root)
    assert chart_statuses(result) == {
        "tooltip": "unsupported",
        "literal": "ok",
        "outside": "ok",
    }
    report = assess(result)
    assert report["summary"] == {"total": 3, "ready": 2, "blocked": 1}
    chart = next(c for c in report["charts"] if c["name"] == "tooltip")
    assert chart["tool_calls"] == []
    assert any("tableau_calculation" in reason for reason in chart["reasons"])
