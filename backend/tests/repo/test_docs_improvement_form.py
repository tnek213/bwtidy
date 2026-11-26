"""The issue form for docs improvements, which the link on every docs page opens."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FORM = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "docs_improvement.yml").read_text())
FIELDS = {item["id"]: item for item in FORM["body"] if "id" in item}


def required(field_id: str) -> bool:
    return bool(FIELDS[field_id].get("validations", {}).get("required"))


def test_the_form_has_the_fields_the_link_fills_in_and_the_suggestion():
    assert {"page", "url", "version", "suggestion"} <= set(FIELDS)


def test_the_link_fields_are_optional_so_the_form_serves_a_general_docs_issue():
    assert not any(required(field_id) for field_id in ("page", "url", "version"))
    assert FIELDS["page"]["type"] == FIELDS["url"]["type"] == FIELDS["version"]["type"] == "input"


def test_the_suggestion_is_required():
    assert FIELDS["suggestion"]["type"] == "textarea"
    assert required("suggestion")


def test_the_form_is_titled_and_labelled_for_docs():
    assert FORM["name"] == "Docs improvement"
    assert FORM["title"] == "Docs: "
    assert FORM["labels"] == ["documentation"]
