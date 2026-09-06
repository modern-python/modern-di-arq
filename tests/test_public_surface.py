import ast
import pathlib
import types

import modern_di_arq
from modern_di_arq import main


def test_public_surface_is_exactly_the_four_documented_symbols() -> None:
    """INVARIANT: the package exports exactly the four symbols the README's API table lists.

    Broken by promoting a helper to a public name, in ``__all__`` or as an unprefixed binding in
    ``__init__`` -- the latter is public whether or not it was meant to be. The surface is an
    adapter's whole semver contract: every name here is one a major release has to keep working,
    and this is the only place that cost is visible before it is paid. arq has no DI seam of its
    own, so the standing pressure is to answer each gap with one more convenience exported from
    here rather than with a provider or a fix upstream in modern-di.
    """
    public = sorted(
        name
        for name, value in vars(modern_di_arq).items()
        if not name.startswith("_") and not isinstance(value, types.ModuleType)
    )

    assert public == ["FromDI", "fetch_di_container", "inject", "setup_di"]
    assert modern_di_arq.__all__ == public


def test_the_adapter_imports_no_arq_symbol() -> None:
    """INVARIANT: the adapter module imports nothing from arq.

    Broken by importing arq for a type annotation, an isinstance check, or to unwrap an
    ``arq.func(...)`` entry. The adapter reaches arq only through shapes -- a ``ctx`` dict, four
    hook callables, a settings class or dict -- which is why one release supports every arq version
    that keeps those shapes, and why ``arq>=0.25,<1`` is a floor rather than a pin against a
    pre-1.0 library. An import is what turns a structural dependency into a versioned one, and it
    costs nothing at the moment it is added: the suite goes on passing against the arq that happens
    to be installed.
    """
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))

    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) and node.module else ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    assert "arq" not in imported
