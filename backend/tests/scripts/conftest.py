import pytest
from devlib import load_script as load


@pytest.fixture(scope="session")
def load_script():
    """Loads a script of scripts/ by name as a module, for example load_script("dev-vault")."""
    return load
