"""Helpers of the integration tests, one module for each theme.

A test imports a helper from the module that holds it (support.cli, support.app, support.vault,
support.restorer). The fixtures stay in conftest.py, which pytest collects and no file imports.
"""
