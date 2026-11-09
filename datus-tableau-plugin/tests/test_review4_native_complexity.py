from __future__ import annotations

from collections import Counter
from xml.etree import ElementTree as ET

from datus_tableau_plugin import native


def test_nested_credentials_are_redacted_with_linear_element_visits(monkeypatch):
    visits = Counter()
    created = []

    class CountedElement(ET.Element):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def iter(self, tag=None):
            for element in super().iter(tag):
                visits[element] += 1
                yield element

    def counted_fromstring(text, **security_flags):
        # Parse the real XML with the same hardened parser and flags as production;
        # only the element factory instruments actual iterator yields.
        parser = native.SafeET.DefusedXMLParser(
            target=ET.TreeBuilder(element_factory=CountedElement), **security_flags
        )
        parser.feed(text)
        return parser.close()

    monkeypatch.setattr(native.SafeET, "fromstring", counted_fromstring)
    depth = 96
    nested = '<a:credentials a:opaque="synthetic-attribute">synthetic-text' * depth
    nested += '<a:value a:identity="synthetic-leaf-attribute">synthetic-leaf-text</a:value>synthetic-leaf-tail'
    nested += "</a:credentials>synthetic-nested-tail" * (depth - 1)
    nested += "</a:credentials>ordinary-parent-tail"
    sibling = '<password value="synthetic-sibling-attribute"><value>synthetic-sibling-text</value>synthetic-sibling-tail</password>ordinary-sibling-tail'
    payload = f"""<datasource xmlns:a="urn:auth" name="source"><connection server="warehouse.example.test" dbname="analytics">{nested}{sibling}<relation type="text">SELECT 7 AS revenue</relation></connection><filter column="region" value="East"/></datasource>""".encode()
    root = native._xml(payload, "datasource")
    # Before any assertion/serialization traversals: an ordinary outer visit and
    # at most one sensitive-subtree visit per element, regardless of nesting.
    assert max(visits.values()) <= 2
    assert sum(visits.values()) <= 2 * len(created)
    assert len(visits) == len(created)
    document = ET.tostring(root, encoding="unicode")
    assert "synthetic-" not in document
    assert "ordinary-parent-tail" in document and "ordinary-sibling-tail" in document
    assert "SELECT 7 AS revenue" in document and 'value="East"' in document
    assert "warehouse.example.test" in document and "analytics" in document
    connection = root.find("connection")
    protected = connection.find("credentials")
    assert protected.attrib == {"{urn:auth}opaque": "[REDACTED]"}
    assert protected.find(".//value").attrib == {"{urn:auth}identity": "[REDACTED]"}
    assert connection.find("password").attrib == {"value": "[REDACTED]"}
    assert all("}" not in element.tag for element in created)
