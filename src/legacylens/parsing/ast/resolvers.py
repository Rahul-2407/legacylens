"""Import resolution: specifier -> project file.

Where the graph gets its truth. Each resolver builds an index once from
the file inventory, then answers per-import:

* Python: dotted-suffix index (so 'corp.billing.invoice' matches
  'src/corp/billing/invoice.py' regardless of the src-root convention),
  plus proper relative-import handling against the importer's location.
* Java: classpath-suffix index; falls back to dropping the last segment,
  which covers static imports and inner classes; wildcard imports resolve
  to the package directory.
* JS/TS: Node-style relative resolution with extension and /index probing;
  bare specifiers are external packages by definition.

A relative import that resolves to nothing is returned as broken=True —
that is a finding, not a silent skip.
"""

import posixpath
from dataclasses import dataclass

from legacylens.parsing.ast.extract import RawImport

_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json")


@dataclass(frozen=True)
class Resolution:
    target: str
    internal: bool
    broken: bool = False


def _external(spec: str) -> Resolution:
    return Resolution(target=spec, internal=False)


def _broken(spec: str) -> Resolution:
    return Resolution(target=spec, internal=False, broken=True)


class PythonImportResolver:
    def __init__(self, py_files: list[str]) -> None:
        self._files = set(py_files)
        self._index: dict[str, list[str]] = {}
        for path in sorted(py_files):
            parts = path[:-3].split("/")           # strip .py
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                dotted = ".".join(parts[i:])
                self._index.setdefault(dotted, []).append(path)

    def resolve(self, importer: str, imp: RawImport) -> Resolution:
        if imp.relative_level:
            return self._resolve_relative(importer, imp)

        for candidate in (
            *(f"{imp.spec}.{name}" for name in imp.from_names if imp.spec),
            imp.spec,
        ):
            hits = self._index.get(candidate)
            if hits:
                return Resolution(target=hits[0], internal=True)
        return _external(imp.spec)

    def _resolve_relative(self, importer: str, imp: RawImport) -> Resolution:
        base_parts = importer.split("/")[:-1]
        ups = imp.relative_level - 1
        if ups > len(base_parts):
            return _broken("." * imp.relative_level + imp.spec)
        base = base_parts[: len(base_parts) - ups]

        module_parts = imp.spec.split(".") if imp.spec else []
        candidates: list[str] = []
        stem = "/".join(base + module_parts)
        if module_parts:
            candidates += [f"{stem}.py", f"{stem}/__init__.py"]
        for name in imp.from_names:
            candidates += [f"{stem}/{name}.py" if stem else f"{name}.py",
                           f"{stem}/{name}/__init__.py" if stem
                           else f"{name}/__init__.py"]
        if not module_parts:
            candidates.append(f"{'/'.join(base)}/__init__.py")

        for candidate in candidates:
            if candidate in self._files:
                return Resolution(target=candidate, internal=True)
        return _broken("." * imp.relative_level + imp.spec)


class JavaImportResolver:
    def __init__(self, java_files: list[str]) -> None:
        self._class_index: dict[str, list[str]] = {}
        self._package_index: dict[str, list[str]] = {}
        for path in sorted(java_files):
            parts = path[:-5].split("/")           # strip .java
            for i in range(len(parts)):
                self._class_index.setdefault(
                    "/".join(parts[i:]), []).append(path)
            pkg_parts = parts[:-1]
            for i in range(len(pkg_parts)):
                self._package_index.setdefault(
                    "/".join(pkg_parts[i:]), []).append(path)

    def resolve(self, importer: str, imp: RawImport) -> Resolution:
        segments = imp.spec.split(".")
        if imp.wildcard:
            hits = self._package_index.get("/".join(segments))
            if hits:
                return Resolution(
                    target=posixpath.dirname(hits[0]), internal=True)
            return _external(imp.spec + ".*")

        for candidate_segments in (segments, segments[:-1]):
            if not candidate_segments:
                continue
            hits = self._class_index.get("/".join(candidate_segments))
            if hits:
                return Resolution(target=hits[0], internal=True)
        return _external(imp.spec)


class JsImportResolver:
    def __init__(self, all_files: list[str]) -> None:
        self._files = set(all_files)

    def resolve(self, importer: str, imp: RawImport) -> Resolution:
        spec = imp.spec
        if not spec.startswith("."):
            # bare specifier: external package; keep only the package name
            parts = spec.split("/")
            package = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            return _external(package)

        base = posixpath.normpath(
            posixpath.join(posixpath.dirname(importer), spec)
        )
        if base.startswith(".."):
            return _broken(spec)
        candidates = [base]
        candidates += [base + ext for ext in _JS_EXTENSIONS]
        candidates += [f"{base}/index{ext}" for ext in _JS_EXTENSIONS]
        for candidate in candidates:
            if candidate in self._files:
                return Resolution(target=candidate, internal=True)
        return _broken(spec)
