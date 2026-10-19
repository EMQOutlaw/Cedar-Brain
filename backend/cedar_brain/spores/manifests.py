"""Parse SKILL.md front matter without executing or importing anything.

Supports the subset of YAML that skill manifests use in practice:
``key: value`` scalars (quoted or bare), ``>``/``|`` block scalars, inline
``[a, b]`` lists, ``- item`` lists and one level of nested mappings.
Anything else is kept as a raw string, never evaluated.
"""

from __future__ import annotations

import re

MAX_MANIFEST_BYTES = 256 * 1024
MAX_FRONTMATTER_LINES = 400
_KEY = re.compile(r"^([A-Za-z0-9_.\-]+):(?:\s+(.*))?$")


class ManifestError(ValueError):
    pass


def _scalar(text: str):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(p) for p in text[1:-1].split(",") if p.strip()]
    if text in ("true", "false"):
        return text == "true"
    return text


def split_frontmatter(content: str) -> tuple[list[str], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], content
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            if i > MAX_FRONTMATTER_LINES:
                raise ManifestError("front matter too long")
            return lines[1:i], "\n".join(lines[i + 1:])
    raise ManifestError("unterminated front matter")


def parse_frontmatter(lines: list[str]) -> dict:
    data: dict = {}
    i = 0

    def block(start: int, indent_min: int) -> tuple[list[str], int]:
        out, j = [], start
        while j < len(lines):
            ln = lines[j]
            if ln.strip() and (len(ln) - len(ln.lstrip())) < indent_min:
                break
            out.append(ln.strip())
            j += 1
        return out, j

    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
            i += 1
            continue
        m = _KEY.match(line.rstrip())
        if not m:
            i += 1
            continue
        key, rest = m.group(1), (m.group(2) or "").strip()
        i += 1
        if rest in (">", "|", ">-", "|-"):
            body, i = block(i, 1)
            data[key] = (" " if rest.startswith(">") else "\n").join(b for b in body if b or rest.startswith("|")).strip()
        elif rest:
            data[key] = _scalar(rest)
        else:
            body, i = block(i, 1)
            items = [b for b in body if b]
            if items and all(b.startswith("- ") or b == "-" for b in items):
                data[key] = [_scalar(b[2:]) for b in items]
            else:
                nested = {}
                for b in items:
                    km = _KEY.match(b)
                    if km:
                        nested[km.group(1)] = _scalar(km.group(2) or "")
                data[key] = nested
    return data


def read_manifest(content: bytes) -> tuple[dict, str]:
    if len(content) > MAX_MANIFEST_BYTES:
        raise ManifestError(f"SKILL.md exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError("SKILL.md is not UTF-8")
    if "\x00" in text:
        raise ManifestError("SKILL.md contains NUL bytes")
    fm, body = split_frontmatter(text)
    return parse_frontmatter(fm), body
