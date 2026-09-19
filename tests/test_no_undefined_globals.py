"""Static guard: every global name used in scripts/ must resolve.

Two real defects motivated this: ``file_ops.py`` called ``time.time()`` in its
progress bar without ever importing ``time`` (big-upload/big-download raised
``NameError`` on the first chunk), and the timeout fix touched modules that use
module-level helpers. A missing import only fails on the code path that runs
it, so compileall and the existing unit tests both stayed green.
"""
import ast
import builtins
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"


def _bound_names(tree):
    """Names bound anywhere in the module: defs, classes, assignments, imports, args."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for arg in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs):
                    bound.add(arg.arg)
                if args.vararg:
                    bound.add(args.vararg.arg)
                if args.kwarg:
                    bound.add(args.kwarg.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _used_global_attributes(tree):
    """Attribute roots read as globals, e.g. ``time`` in ``time.time()``."""
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if isinstance(node.value.ctx, ast.Load):
                used.add(node.value.id)
    return used


class NoUndefinedGlobalTest(unittest.TestCase):
    def test_scripts_have_no_undefined_global_attributes(self):
        problems = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
            bound = _bound_names(tree) | set(dir(builtins))
            for name in sorted(_used_global_attributes(tree) - bound):
                problems.append(f"{path.relative_to(SCRIPTS.parent).as_posix()}: {name}")
        self.assertEqual(
            problems,
            [],
            "undefined global name(s) reached via attribute access:\n  " + "\n  ".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
