"""The answer to a refused request body, for the gateway and the workers."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core import PydanticCustomError

## the error type of our own validators, whose text we write and never fill with a value
OWN_TYPE = "bwtidy"

## pydantic types whose text never quotes the input. A type missing here only costs a less
## precise message after a pydantic upgrade, never a leak.
TEXT_NEVER_QUOTES_INPUT = frozenset(
    {
        "missing",
        "string_type",
        "int_parsing",
        "int_type",
        "float_parsing",
        "float_type",
        "bool_parsing",
        "bool_type",
        "literal_error",
        "enum",
        "list_type",
        "dict_type",
        "model_type",
        "model_attributes_type",
        "string_too_short",
        "string_too_long",
        "too_short",
        "too_long",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "multiple_of",
        "json_invalid",
        "json_type",
        "union_tag_not_found",
        "extra_forbidden",
        "string_pattern_mismatch",
    }
)

## what any other type gets: value_error, assertion_error and uuid_parsing quote the input
FIXED_MESSAGE = "The value is not valid."


def refusal(message: str) -> PydanticCustomError:
    """What our own validators raise instead of ValueError, so that message is kept as written."""
    return PydanticCustomError(OWN_TYPE, "{message}", {"message": message})


def safe_message(error: Any) -> str:
    """The text of one pydantic error (an item of errors()) without any quote of the input.

    pydantic's own text stays for the types in TEXT_NEVER_QUOTES_INPUT, our own errors (OWN_TYPE,
    see refusal) keep what the validator wrote, a tag error is rebuilt from its ctx without the
    tag and every other type gets FIXED_MESSAGE. The place of an error (loc) is not changed: a
    dict key in it is a coordinate name, not a secret value.
    """
    kind = error["type"]
    if kind == OWN_TYPE or kind in TEXT_NEVER_QUOTES_INPUT:
        return str(error["msg"])
    if kind == "union_tag_invalid":
        ctx = error.get("ctx") or {}
        return (
            f"{ctx.get('discriminator', 'The tag')} should be one of {ctx.get('expected_tags', '')}"
        )
    return FIXED_MESSAGE


def never_echo_a_refused_body(app: FastAPI) -> None:
    """A 422 like FastAPI's, which names the field and the problem but leaves the body out.

    Each error has type, loc and safe_message as msg. FastAPI's default answer has input and ctx
    besides, and those can hold the refused value.
    """

    @app.exception_handler(RequestValidationError)
    async def without_input(_request: Request, error: RequestValidationError) -> JSONResponse:
        detail = [
            {"type": entry["type"], "loc": entry["loc"], "msg": safe_message(entry)}
            for entry in error.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)
