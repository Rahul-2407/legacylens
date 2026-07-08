"""Language detection.

Three passes, cheapest first: exact filename (Dockerfile, Makefile),
extension map, then shebang sniffing for extensionless executables. This is
inventory-level tagging — deeper, content-aware classification belongs to
the parsing layer (Modules 3-4), which knows that pom.xml is not merely XML
but a Maven manifest.
"""

from pathlib import PurePosixPath

EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".kt": "kotlin",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".scala": "scala",
    ".groovy": "groovy",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".properties": "properties",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "config",
    ".env": "config",
    ".gradle": "groovy",
    ".tf": "terraform",
    ".proto": "protobuf",
    ".md": "markdown",
    ".rst": "markdown",
    ".txt": "text",
    ".csv": "data",
    ".ipynb": "jupyter",
}

FILENAME_MAP: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "jenkinsfile": "groovy",
    "gemfile": "ruby",
    "rakefile": "ruby",
    "vagrantfile": "ruby",
    "procfile": "config",
    ".gitignore": "config",
    ".dockerignore": "config",
    ".editorconfig": "config",
}

_SHEBANG_HINTS: tuple[tuple[str, str], ...] = (
    ("python", "python"),
    ("node", "javascript"),
    ("bash", "shell"),
    ("sh", "shell"),
    ("ruby", "ruby"),
    ("perl", "perl"),
)


def detect_language(rel_path: str, head: bytes | None = None) -> str | None:
    """Best-effort language tag for a file, or None if unknown/binary."""
    name = PurePosixPath(rel_path).name.lower()

    if name in FILENAME_MAP:
        return FILENAME_MAP[name]
    # "Dockerfile.prod" style variants
    if name.startswith("dockerfile."):
        return "dockerfile"

    suffix = PurePosixPath(name).suffix
    if suffix in EXTENSION_MAP:
        return EXTENSION_MAP[suffix]

    if head and head.startswith(b"#!"):
        try:
            first_line = head.split(b"\n", 1)[0].decode("utf-8", "ignore")
        except Exception:  # pragma: no cover — decode with 'ignore' is total
            return None
        for token, language in _SHEBANG_HINTS:
            if token in first_line:
                return language

    return None
