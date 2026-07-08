"""Import extraction from tree-sitter ASTs.

Manual node walks against stable node types — deliberately no tree-sitter
query strings, whose API has churned across binding versions. Each
extractor returns RawImports; resolution to project files happens in
resolvers.py.
"""

from dataclasses import dataclass, field

from tree_sitter import Node


@dataclass(frozen=True)
class RawImport:
    spec: str                       # module/package/path as written
    line: int
    relative_level: int = 0         # python: number of leading dots
    from_names: tuple[str, ...] = ()  # python: names after 'import'
    wildcard: bool = False          # java: import com.x.*


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace")


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _walk(root: Node, types: frozenset[str]) -> list[Node]:
    found, stack = [], [root]
    while stack:
        node = stack.pop()
        if node.type in types:
            found.append(node)
        stack.extend(node.children)
    return sorted(found, key=lambda n: n.start_byte)


# ------------------------------------------------------------------- python

def extract_python(root: Node) -> list[RawImport]:
    imports: list[RawImport] = []
    for node in _walk(root, frozenset({"import_statement",
                                       "import_from_statement"})):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    imports.append(RawImport(_text(child), _line(node)))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        imports.append(RawImport(_text(name), _line(node)))
            continue

        # from X import a, b  /  from ..pkg import c
        module, level, names = "", 0, []
        seen_import_kw = False
        for child in node.children:
            if child.type == "import":
                seen_import_kw = True
            elif child.type == "relative_import" and not seen_import_kw:
                inner = [c for c in child.children if c.type == "dotted_name"]
                module = _text(inner[0]) if inner else ""
                level = _text(child).count(".") - module.count(".")
            elif child.type == "dotted_name" and not seen_import_kw:
                module = _text(child)
            elif seen_import_kw and child.type == "dotted_name":
                names.append(_text(child))
            elif seen_import_kw and child.type == "aliased_import":
                name = child.child_by_field_name("name")
                if name is not None:
                    names.append(_text(name))
        imports.append(RawImport(
            spec=module,
            line=_line(node),
            relative_level=level,
            from_names=tuple(names),
        ))
    return imports


# --------------------------------------------------------------------- java

def extract_java(root: Node) -> list[RawImport]:
    imports = []
    for node in _walk(root, frozenset({"import_declaration"})):
        spec, wildcard = None, False
        for child in node.children:
            if child.type == "scoped_identifier" or child.type == "identifier":
                spec = _text(child)
            elif child.type == "asterisk":
                wildcard = True
        if spec:
            imports.append(RawImport(spec, _line(node), wildcard=wildcard))
    return imports


# ------------------------------------------------------------ javascript/ts

def _string_value(node: Node) -> str | None:
    if node.type in ("string", "template_string"):
        return _text(node).strip("'\"`")
    return None


def extract_javascript(root: Node) -> list[RawImport]:
    imports = []
    for node in _walk(root, frozenset({"import_statement",
                                       "export_statement",
                                       "call_expression"})):
        if node.type in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            if source is not None and (value := _string_value(source)):
                imports.append(RawImport(value, _line(node)))
            continue

        fn = node.child_by_field_name("function")
        if fn is None or _text(fn) not in ("require", "import"):
            continue
        args = node.child_by_field_name("arguments")
        if args is None:
            continue
        for arg in args.children:
            if (value := _string_value(arg)) is not None:
                imports.append(RawImport(value, _line(node)))
                break
    return imports


EXTRACTORS = {
    "python": extract_python,
    "java": extract_java,
    "javascript": extract_javascript,
    "typescript": extract_javascript,  # same node shapes for imports
}
