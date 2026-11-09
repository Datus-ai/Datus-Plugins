from collections import Counter
import json
from xml.etree import ElementTree as ET

from datus_tableau_plugin import native


def test_secret_property_name_redacts_values_text_nested_content_and_native_document():
    payload = b"""<datasource xmlns:a="urn:synthetic" name="source"><connection>
      <property name="password" value="synthetic-attribute-secret">synthetic-text-secret<value opaque="synthetic-nested-attribute">synthetic-nested-text</value>synthetic-child-tail</property>ordinary-parent-tail
      <a:property a:name="PersonalAccessTokenSecret" a:value="synthetic-namespaced-secret">synthetic-namespaced-text</a:property>ordinary-namespaced-tail
      <property name="dbname" value="analytics">ordinary-property-text</property>
      <relation type="text">SELECT 7 AS revenue</relation>
    </connection><object-graph><property name="password" value="synthetic-asset-secret"/></object-graph></datasource>"""
    result = native.parse_datasource(payload, datasource_id="fixture")
    serialized = json.dumps(result)
    assert "synthetic-" not in serialized
    assert "ordinary-parent-tail" in serialized
    assert "ordinary-namespaced-tail" in serialized
    assert "ordinary-property-text" in serialized
    assert "analytics" in serialized and "SELECT 7 AS revenue" in serialized
    definition = next(a for a in result["assets"] if a["kind"] == "dataset")[
        "definition"
    ]
    assert "[REDACTED]" in json.dumps(definition)
    assert "[REDACTED]" in next(iter(result["documents"].values()))


def test_nested_secret_properties_keep_redaction_element_visits_linear(monkeypatch):
    visits, created = Counter(), []

    class CountedElement(ET.Element):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def iter(self, tag=None):
            for element in super().iter(tag):
                visits[element] += 1
                yield element

    def counted_fromstring(text, **flags):
        parser = native.SafeET.DefusedXMLParser(
            target=ET.TreeBuilder(element_factory=CountedElement), **flags
        )
        parser.feed(text)
        return parser.close()

    monkeypatch.setattr(native.SafeET, "fromstring", counted_fromstring)
    nested = '<a:property a:name="password" a:value="synthetic-secret">' * 64
    nested += "<value>synthetic-leaf</value>"
    nested += "</a:property>" * 64
    root = native._xml(
        (
            '<datasource xmlns:a="urn:synthetic">'
            + nested
            + "ordinary-tail</datasource>"
        ).encode(),
        "datasource",
    )
    assert len(visits) == len(created)
    assert max(visits.values()) <= 2
    assert sum(visits.values()) <= 2 * len(created)
    serialized = ET.tostring(root, encoding="unicode")
    assert "synthetic-secret" not in serialized and "synthetic-leaf" not in serialized
    assert "ordinary-tail" in serialized
