import pytest
from datus_tableau_plugin import native
from test_calculation_dependencies import parse, workbook


class CountedText(str):
    reads = 0

    def __getitem__(self, key):
        type(self).reads += 1
        return super().__getitem__(key)


def test_unclosed_bracket_scan_is_linear_and_rejected_without_discarding_references():
    for size in (64, 256, 1024):
        text = CountedText("[" * size)
        CountedText.reads = 0
        with pytest.raises(ValueError, match="Unclosed Tableau field reference"):
            native._field_references(text)
        assert CountedText.reads <= 4 * size
    root = workbook(
        {"sales": {"Amount": None, "Used": "[" * 256 + " TOTAL(0)"}},
        [("consumer", ["sales"], "[sales].[Used]", None)],
    )
    with pytest.raises(ValueError, match="Unclosed Tableau field reference"):
        parse(root)


def test_formula_scanner_keeps_escaped_fields_qualified_scope_literals_and_comments():
    text = "[sa]]les] . [Amount]]USD] + [Plain] + 1e3 + .5E-2 + \"[Hidden]\" + 'it\\'s [Other]' // [Comment]\n /* [AlsoHidden] */ + Bare"
    assert native._field_references(text, {"[Bare]": None}) == [
        ("sa]les", "[Amount]]USD]"),
        (None, "[Plain]"),
        (None, "[Bare]"),
    ]
    assert not native._complex_formula(
        '[TOTAL(SUM)] + "TOTAL(0)" /* {FIXED : [Hidden]} */'
    )
    assert native._complex_formula("TOTAL(SUM([Amount]))")


def test_oversized_formula_refuses_before_scanning_and_cannot_become_ready():
    text = CountedText("x" * (native.MAX_FORMULA_BYTES + 1))
    CountedText.reads = 0
    with pytest.raises(ValueError, match="byte resource limit"):
        native._field_references(text)
    assert CountedText.reads == 0
