"""Assertions on text that may hold a vault value.

A plain `assert value not in text` makes pytest print both operands when it
fails, so a leak would be repeated in the test output. These helpers fail with
a message that names only what was searched.
"""


## no values in a failure's text: pytest prints the operands of a failed assert
def assert_absent[T: (str, bytes)](value: T, text: T, what: str) -> None:
    ## a hidden frame keeps its arguments, the value and the text, out of the traceback
    __tracebackhide__ = True
    if value in text:
        raise AssertionError(f"{what} holds the value")


## a console shows its last line in a failure only when it is a prompt the test knows
def last_prompt(console: str, prompts: tuple[str, ...]) -> str:
    last = console.rsplit("\n", 1)[-1]
    for prompt in prompts:
        if prompt in last:
            return prompt
    return "none of the known prompts"


## no values in a failure's text: pytest prints the operands of a failed assert
def assert_present[T: (str, bytes)](value: T, text: T, what: str) -> None:
    ## a hidden frame keeps its arguments, the value and the text, out of the traceback
    __tracebackhide__ = True
    if value not in text:
        raise AssertionError(f"{what} lacks the value")
