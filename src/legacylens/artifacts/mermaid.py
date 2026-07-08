"""Mermaid diagram generation — deterministic, from graph data only.

Diagrams NEVER pass through the LLM: a diagram is a display of facts, and
facts are not paraphrased. Both generators cap what they draw — a 5,000-
node hairball communicates nothing — and say so in a legend node, keeping
the honesty rule even inside pictures.
"""

import re

from legacylens.graph.algorithms import ArchitectureReport
from legacylens.parsing.ast.module_graph import ModuleGraph

MAX_GRAPH_NODES = 40
MAX_WAVE_ITEMS = 8

_UNSAFE = re.compile(r"[^\w]")


def _label(path: str) -> str:
    parts = path.split("/")
    short = "/".join(parts[-2:]) if len(parts) > 1 else path
    return short.replace('"', "'")


def module_graph_mermaid(
    graph: ModuleGraph,
    report: ArchitectureReport | None = None,
    max_nodes: int = MAX_GRAPH_NODES,
) -> str:
    """Top-coupling subgraph of internal imports; cycle members styled."""
    metrics = report.metrics if report else {}

    def weight(path: str) -> int:
        m = metrics.get(path)
        return (m.fan_in + m.fan_out) if m else 0

    connected = sorted(
        {e.source for e in graph.internal_edges}
        | {e.target for e in graph.internal_edges},
        key=lambda p: (-weight(p), p),
    )
    selected = connected[:max_nodes]
    node_id = {path: f"n{i}" for i, path in enumerate(selected)}
    cycle_members = {p for c in (report.cycles if report else []) for p in c}

    lines = ["flowchart TD"]
    for path in selected:
        lines.append(f'    {node_id[path]}["{_label(path)}"]')
    seen: set[tuple[str, str]] = set()
    for edge in graph.internal_edges:
        key = (edge.source, edge.target)
        if (key in seen or edge.source not in node_id
                or edge.target not in node_id
                or edge.source == edge.target):
            continue
        seen.add(key)
        lines.append(f"    {node_id[edge.source]} --> "
                     f"{node_id[edge.target]}")

    styled = [node_id[p] for p in selected if p in cycle_members]
    if styled:
        lines.append("    classDef cycle fill:#fde2e2,stroke:#c0392b,"
                     "stroke-width:2px")
        lines.append(f"    class {','.join(styled)} cycle")
    omitted = len(connected) - len(selected)
    if omitted > 0:
        lines.append(f'    legend["(+{omitted} lower-coupling modules '
                     'omitted for readability)"]')
    return "\n".join(lines)


def waves_mermaid(report: ArchitectureReport) -> str:
    """Migration order: one subgraph per wave, left to right."""
    lines = ["flowchart LR"]
    node_counter = 0
    for wave_index, wave in enumerate(report.waves):
        lines.append(f'    subgraph W{wave_index}["Wave {wave_index}'
                     f' ({len(wave)} modules)"]')
        for path in wave[:MAX_WAVE_ITEMS]:
            lines.append(f'        w{node_counter}["{_label(path)}"]')
            node_counter += 1
        if len(wave) > MAX_WAVE_ITEMS:
            lines.append(f'        w{node_counter}["…+'
                         f'{len(wave) - MAX_WAVE_ITEMS} more"]')
            node_counter += 1
        lines.append("    end")
    for wave_index in range(len(report.waves) - 1):
        lines.append(f"    W{wave_index} --> W{wave_index + 1}")
    return "\n".join(lines)
