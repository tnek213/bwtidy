"""Where the repo and its scripts are, and the loader of a script by its path."""

import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parent
DEV_VAULT = SCRIPTS / "dev-vault"


def load_script(name: str) -> ModuleType:
    """The script scripts/NAME as a module, run anew on every call."""
    ## the scripts have no .py suffix, so the loader must be named outright
    path = SCRIPTS / name
    module_name = name.replace("-", "_")
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
