"""tree-sitter parser factory.

Parsers are built lazily and cached — grammar loading is cheap but not
free, and a 50k-file repo should pay it once per language, not per file.
TSX and JSX need care: .tsx uses the dedicated tsx grammar; .jsx parses
fine under the javascript grammar.
"""

from functools import lru_cache
from pathlib import PurePosixPath

from tree_sitter import Language, Parser

_SUPPORTED = {"python", "java", "javascript", "typescript"}


@lru_cache(maxsize=None)
def _language(key: str) -> Language:
    if key == "python":
        import tree_sitter_python as mod
        return Language(mod.language())
    if key == "java":
        import tree_sitter_java as mod
        return Language(mod.language())
    if key == "javascript":
        import tree_sitter_javascript as mod
        return Language(mod.language())
    if key == "typescript":
        import tree_sitter_typescript as mod
        return Language(mod.language_typescript())
    if key == "tsx":
        import tree_sitter_typescript as mod
        return Language(mod.language_tsx())
    raise KeyError(key)


@lru_cache(maxsize=None)
def _parser(key: str) -> Parser:
    return Parser(_language(key))


def parser_for(language: str, rel_path: str) -> Parser | None:
    """Return a cached parser for the file, or None if unsupported."""
    if language not in _SUPPORTED:
        return None
    if language == "typescript" and PurePosixPath(rel_path).suffix == ".tsx":
        return _parser("tsx")
    return _parser(language)
