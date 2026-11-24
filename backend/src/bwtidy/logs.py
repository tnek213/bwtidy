"""The log format of the gateway and the workers: every line starts with its time."""

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
PACKAGE = "bwtidy"


def setup_logging() -> None:
    """Our own lines from INFO up, other libraries' from WARNING up.

    INFO of a library can name what passes through it, like the URLs of httpx's requests.
    """
    logging.basicConfig(level=logging.WARNING, format=FORMAT)
    logging.getLogger(PACKAGE).setLevel(logging.INFO)


def uvicorn_log_config() -> dict[str, Any]:
    """uvicorn's own logging with the time in front of its default and its access format."""
    config = copy.deepcopy(LOGGING_CONFIG)
    for formatter in config["formatters"].values():
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
    return config
