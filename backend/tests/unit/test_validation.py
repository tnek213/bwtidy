"""A 422 never quotes a refused value: safe_message, and a sweep of every route with a sentinel."""

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bwtidy.operations import InvalidOperation, parse_operations
from bwtidy.sections.generator import InvalidGenerator, parse_options
from bwtidy.sections.templates import InvalidTemplates, parse_templates
from bwtidy.validation import FIXED_MESSAGE, OWN_TYPE, refusal, safe_message
from tests.support.app_env import AppEnv
from tests.support.gateway_env import make_env as make_gateway_env
from tests.support.section_changes import add_template, changes
from tests.support.sockets import short_tmp_path

SENTINEL = "sentinel-value-that-must-not-come-back"


def error(kind: str, msg: str, **ctx: Any) -> dict[str, Any]:
    return {"type": kind, "loc": ("body", "x"), "msg": msg, "input": SENTINEL, "ctx": ctx}


def test_safe_message_keeps_the_text_of_a_type_that_never_quotes_the_input():
    entry = error("string_type", "Input should be a valid string")
    assert safe_message(entry) == "Input should be a valid string"
    assert safe_message(error("missing", "Field required")) == "Field required"


@pytest.mark.parametrize(
    ("kind", "msg"),
    [
        ("value_error", f"Value error, {SENTINEL}"),
        ("assertion_error", f"Assertion failed, {SENTINEL}"),
        ("uuid_parsing", f"Input should be a valid UUID, invalid character: found `{SENTINEL}`"),
        ("a_type_pydantic_adds_later", f"quotes {SENTINEL}"),
    ],
)
def test_safe_message_gives_every_other_type_a_fixed_text(kind: str, msg: str):
    text = safe_message(error(kind, msg))
    leaked = SENTINEL in text
    assert not leaked
    assert text == FIXED_MESSAGE


def test_safe_message_rebuilds_a_tag_error_without_the_tag():
    entry = error(
        "union_tag_invalid",
        f"Input tag '{SENTINEL}' found using 'kind' does not match any of the expected tags",
        discriminator="'kind'",
        tag=SENTINEL,
        expected_tags="'a', 'b'",
    )
    text = safe_message(entry)
    leaked = SENTINEL in text
    assert not leaked
    assert text == "'kind' should be one of 'a', 'b'"


def test_safe_message_keeps_what_our_own_validator_wrote():
    caught = refusal("the datatype of name is no id")
    entry = error(OWN_TYPE, caught.message())
    assert safe_message(entry) == "the datatype of name is no id"


def test_the_hand_parsers_leave_a_refused_tag_out():
    with pytest.raises(InvalidOperation) as operations:
        parse_operations([{"kind": SENTINEL}])
    with pytest.raises(InvalidGenerator) as generator:
        parse_options({"type": SENTINEL})
    with pytest.raises(InvalidTemplates) as templates:
        parse_templates(
            [{"name": "t", "type": 1, "coordinates": [{"kind": SENTINEL}]}],
        )
    for raised in (operations, generator, templates):
        leaked = SENTINEL in str(raised.value)
        assert not leaked
        assert "should be one of" in str(raised.value)


def test_the_texts_of_our_own_validators_reach_the_hand_parsers():
    with pytest.raises(InvalidTemplates, match="the datatype of name is no id"):
        parse_templates(
            [
                {
                    "name": "t",
                    "type": 1,
                    "coordinates": [{"kind": "path", "path": "name", "datatype": 5}],
                }
            ]
        )
    with pytest.raises(InvalidGenerator, match="at least one of A-Z"):
        parse_options(
            {
                "type": "password",
                "uppercase": False,
                "lowercase": False,
                "number": False,
                "special": False,
            }
        )


def filled(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "login-1", path)


def body_fields(schema: dict[str, Any], components: dict[str, Any]) -> list[str]:
    """The property names of a request body's model, none for a plain object."""
    if "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return list(schema.get("properties", {}))


def bodies(fields: list[str]) -> list[dict[str, Any]]:
    """Request arguments, each with the sentinel where a value would go."""
    jsons: list[Any] = [
        SENTINEL,
        [SENTINEL],
        {"kind": SENTINEL},
        {"type": SENTINEL},
        {"operations": [{"kind": SENTINEL}]},
        {"templates": [{"name": "t", "type": 1, "coordinates": [{"kind": SENTINEL}]}]},
        {name: SENTINEL for name in fields},
        {name: [SENTINEL] for name in fields},
        {name: {SENTINEL: SENTINEL} for name in fields},
        {SENTINEL: SENTINEL},
    ]
    sent: list[dict[str, Any]] = [{"json": body} for body in jsons]
    sent.append({"content": '{"x": "' + SENTINEL, "headers": {"Content-Type": "application/json"}})
    sent.append({"content": SENTINEL, "headers": {"Content-Type": "text/plain"}})
    return sent


def sweep(
    client: TestClient, app: FastAPI, ready: Callable[[], None] = lambda: None
) -> dict[str, set[int]]:
    """Send the sentinel into every route that takes a body or a query parameter.

    The routes come from the OpenAPI document, since FastAPI keeps its route tree private.
    ready runs before each request, to log in again after a refused password locked the vault.
    Returns the statuses by route. Fails at the first answer that holds the sentinel.
    """
    document = app.openapi()
    components = document.get("components", {}).get("schemas", {})
    statuses: dict[str, set[int]] = {}
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            query = [p["name"] for p in operation.get("parameters", []) if p["in"] == "query"]
            body = operation.get("requestBody")
            if not (body or query):
                continue
            fields: list[str] = []
            if body:
                schema = next(iter(body["content"].values()))["schema"]
                fields = body_fields(schema, components)
            params = {name: SENTINEL for name in query}
            key = f"{method.upper()} {path}"
            for sent in bodies(fields) if body else [{}]:
                ready()
                response = client.request(method, filled(path), params=params, **sent)
                leaked = SENTINEL in response.text
                assert not leaked, f"{key} answered {response.status_code} with the sentinel"
                statuses.setdefault(key, set()).add(response.status_code)
    return statuses


def test_no_worker_route_quotes_a_refused_value(tmp_path: Path):
    with AppEnv(tmp_path) as env:
        env.unlock()

        def logged_in() -> None:
            if env.client.get("/api/session").json()["state"] != "unlocked":
                env.unlock()

        statuses = sweep(env.client, env.app, logged_in)
    assert len(statuses) > 15
    unrefused = [key for key, seen in statuses.items() if 422 not in seen]
    ## these take no value that can be refused: any string is a column, and the output is
    ## a 404 until a Bitwarden CLI login is under way
    assert unrefused == ["GET /api/bw-login/output", "GET /api/items/{item_id}/secret"]


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    """A tmp_path whose end removes the gateway's socket directories too."""
    yield from short_tmp_path("gw")


def test_no_gateway_route_quotes_a_refused_value(short_tmp: Path):
    for env in make_gateway_env(short_tmp):
        statuses = sweep(env.client, env.app)
        assert "POST /gateway/account" in statuses
        assert [key for key, seen in statuses.items() if 422 not in seen] == []


def test_a_refused_tag_is_not_quoted_by_the_routes_that_parse_by_hand(tmp_path: Path):
    with AppEnv(tmp_path) as env:
        env.unlock()
        sent = [
            ("POST", "/api/queue", {"operations": [{"kind": SENTINEL}]}),
            ("POST", "/api/operations", {"operations": [{"kind": SENTINEL}]}),
            ("PUT", "/api/generator/default", {"type": SENTINEL}),
            ("POST", "/api/generator/example", {"type": SENTINEL}),
            (
                "PUT",
                "/api/templates",
                changes(
                    add_template({"name": "t", "type": 1, "coordinates": [{"kind": SENTINEL}]})
                ),
            ),
            ("PUT", "/api/templates", changes({"op": SENTINEL})),
            ("PUT", "/api/datatypes", changes({"op": SENTINEL})),
        ]
        for method, path, body in sent:
            response = env.client.request(method, path, json=body)
            assert response.status_code == 422, path
            leaked = SENTINEL in json.dumps(response.json())
            assert not leaked, path
            assert "should be one of" in response.text, path
