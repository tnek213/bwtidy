"""devlib: what the scripts share, each script a command line over its modules.

  paths     where the repo and the scripts are, load_script for a script by its path
  versions  the version of backend/pyproject.toml and frontend/package.json
  stacks    the dev stacks of a worktree: numbers, env files, ports, projects, run_stacks
  compose   docker compose of a stack, its certificate and the wait for Vaultwarden
  snapshot  the snapshot image of a seeded stack
  seed      the dev vault's accounts and content, through the web vault and bw
  gateway   the gateway in a stack's app container: start, signal, stop, run_backend
  health    /api/health: healthy, health_text, health_version, wait_healthy
  suites    the options and test paths of scripts/e2e and scripts/integration, the sleep lock
  git       git, the commit a build shows, check_checkout
  venv      in_backend_venv and the start of a script in the backend venv

The modules run nothing when imported. The scripts import the package from their own
directory, which is on sys.path when they run. scripts/docs-site imports none of it: the
Dockerfile's docs stage copies that script alone.
"""

from devlib.gateway import (
    BACKEND,
    GATEWAY_BIN,
    LEFTOVERS,
    SIGNAL_BACKEND,
    STOP_TIMEOUT,
    SYNC,
    WORKER_BIN,
    run_backend,
    signal_backend,
    stop_backend,
    sync_venv,
)
from devlib.health import START_TIMEOUT, health_text, healthy, wait_healthy
from devlib.paths import DEV_VAULT, ROOT, load_script
from devlib.stacks import dev_vault_run
from devlib.suites import (
    INHIBITED,
    SUITES,
    pytest_args,
    reexec_inhibited,
    split_options,
    under,
    wants_help,
)
from devlib.versions import package_json_version, pyproject_version, semver, version_mismatch

__all__ = [
    "BACKEND",
    "DEV_VAULT",
    "GATEWAY_BIN",
    "INHIBITED",
    "LEFTOVERS",
    "ROOT",
    "SIGNAL_BACKEND",
    "START_TIMEOUT",
    "STOP_TIMEOUT",
    "SUITES",
    "SYNC",
    "WORKER_BIN",
    "dev_vault_run",
    "health_text",
    "healthy",
    "load_script",
    "package_json_version",
    "pyproject_version",
    "pytest_args",
    "reexec_inhibited",
    "run_backend",
    "semver",
    "signal_backend",
    "split_options",
    "stop_backend",
    "sync_venv",
    "under",
    "version_mismatch",
    "wait_healthy",
    "wants_help",
]
