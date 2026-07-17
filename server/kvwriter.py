"""Minimal Valve KeyValues writer.

csgo_gc reads its inventory/config as Valve KeyValues (.txt) files. This writer
emits the tab-indented, double-quoted format that csgo_gc's parser accepts.

We keep it deliberately simple: values are coerced to strings and any embedded
double quotes / backslashes are stripped (csgo_gc item fields never legitimately
need them, and this avoids escaping ambiguities across parsers).
"""

from __future__ import annotations

from typing import Union

# A KeyValues node is either a leaf string value or a nested dict of more nodes.
KVNode = Union[str, int, float, "dict[str, object]"]


def _sanitize(text: str) -> str:
    # KeyValues tokens are delimited by double quotes; drop characters that would
    # break tokenization rather than risk parser-specific escape handling.
    return text.replace("\\", "").replace('"', "")


def _write_node(out: list, key: str, value: KVNode, depth: int) -> None:
    indent = "\t" * depth
    safe_key = _sanitize(str(key))

    if isinstance(value, dict):
        out.append(f'{indent}"{safe_key}"')
        out.append(f"{indent}{{")
        for child_key, child_value in value.items():
            _write_node(out, child_key, child_value, depth + 1)
        out.append(f"{indent}}}")
    else:
        if isinstance(value, bool):
            value = 1 if value else 0
        safe_value = _sanitize(str(value))
        out.append(f'{indent}"{safe_key}"\t\t"{safe_value}"')


def dumps(root_key: str, tree: dict) -> str:
    """Serialize a single top-level KeyValues block, e.g. dumps("inventory", {...})."""
    out: list = []
    _write_node(out, root_key, tree, 0)
    return "\n".join(out) + "\n"
