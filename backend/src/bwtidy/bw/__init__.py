"""Talking to the Bitwarden CLI: HTTP to bw serve, its processes and bw's JSON.

It knows nothing of bwtidy's vocabulary.
"""

from bwtidy.bw.client import BwClient, BwError, BwUnavailable
from bwtidy.bw.login import (
    BwLogin,
    BwLoginState,
    LoginRefused,
    NoLogin,
    NotFinished,
    NotRunning,
    WrongAccount,
)
from bwtidy.bw.process import run_cli
from bwtidy.bw.revision import same_revision
from bwtidy.bw.serve import BwServeProcess

__all__ = [
    "BwClient",
    "BwError",
    "BwLogin",
    "BwLoginState",
    "BwServeProcess",
    "BwUnavailable",
    "LoginRefused",
    "NoLogin",
    "NotFinished",
    "NotRunning",
    "WrongAccount",
    "run_cli",
    "same_revision",
]
