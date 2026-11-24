"""Every backend package's __init__.py follows the rule of docs/architecture.md, Backend packages.

A package of the first kind re-exports exactly what modules outside it import from it, and they
import from the package, never from a module of it. A package of the second kind re-exports
nothing and is imported by module. No module imports another module's underscore name.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "bwtidy"
SURFACES = ("bw", "vault", "backups", "writes", "mcp")
BY_MODULE = ("items", "queue", "api", "gateway")
## defines Section, SECTIONS and SectionChange, its section modules are imported by module
SECTIONS = "sections"


def modules() -> list[tuple[str, ast.Module]]:
    """Every source module with its dotted name, its package's __init__ as the package's name."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = ("bwtidy", *path.relative_to(PACKAGE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.append((".".join(parts), ast.parse(path.read_text())))
    return found


def imports(tree: ast.Module) -> list[tuple[str, str | None]]:
    """(module, name) of every import, name None for a plain `import module`."""
    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, None) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found += [(node.module, alias.name) for alias in node.names]
    return found


def inside(module: str, package: str) -> bool:
    return module == f"bwtidy.{package}" or module.startswith(f"bwtidy.{package}.")


def submodules(package: str) -> set[str]:
    return {path.stem for path in (PACKAGE / package).glob("*.py") if path.stem != "__init__"}


def exported(package: str) -> set[str]:
    tree = ast.parse((PACKAGE / package / "__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            assert isinstance(node.value, ast.List)
            return {
                str(element.value)
                for element in node.value.elts
                if isinstance(element, ast.Constant)
            }
    return set()


def imported_from_outside(package: str) -> set[str]:
    return {
        name
        for module, tree in modules()
        if not inside(module, package)
        for imported, name in imports(tree)
        if imported == f"bwtidy.{package}" and name is not None
    }


def test_a_surface_exports_exactly_what_the_app_imports_from_it():
    wrong = {
        package: {
            "imported, not exported": sorted(imported_from_outside(package) - exported(package)),
            "exported, never imported": sorted(exported(package) - imported_from_outside(package)),
        }
        for package in (*SURFACES, SECTIONS)
        if imported_from_outside(package) != exported(package)
    }
    assert not wrong


def test_no_module_imports_a_module_of_a_surface():
    found = [
        f"{module} imports {imported}" + (f".{name}" if name in submodules(package) else "")
        for package in SURFACES
        for module, tree in modules()
        if not inside(module, package)
        for imported, name in imports(tree)
        if imported.startswith(f"bwtidy.{package}.")
        or (imported == f"bwtidy.{package}" and name in submodules(package))
    ]
    assert not found


def test_a_package_imported_by_module_re_exports_nothing():
    found = [
        f"bwtidy.{package} imports {imported}"
        for package in BY_MODULE
        for imported, _ in imports(ast.parse((PACKAGE / package / "__init__.py").read_text()))
    ]
    assert not found


def test_no_module_imports_an_underscore_name_of_another():
    found = [
        f"{module} imports {name} from {imported}"
        for module, tree in modules()
        for imported, name in imports(tree)
        if imported.startswith("bwtidy") and name is not None and name.startswith("_")
    ]
    assert not found
