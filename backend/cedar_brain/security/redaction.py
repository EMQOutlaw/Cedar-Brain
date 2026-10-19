"""Secret redaction for logs, events and stored results.

Redaction is a safety net, not a guarantee: it catches well-known token
shapes. Agent output is still treated as untrusted and bounded.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
]
# key=value / "key": "value" forms for obviously secret keys.
_KV = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_\-])  # start only at a token boundary: keeps matching linear on long runs
    (?P<key>["']?[A-Za-z0-9_\-]{0,40}?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|
        client[_-]?secret|secret[_-]?key|password|passwd|bearer)["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<q>["']?)(?P<val>[^\s"',}]{6,})(?P=q)
    """
)
_BEARER = re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+|bearer\s+)[A-Za-z0-9._\-~+/]{12,}=*")


def redact(text: str) -> str:
    if not text:
        return text
    for pat in _PATTERNS:
        text = pat.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _KV.sub(lambda m: f"{m.group('key')}{m.group('sep')}{m.group('q')}{REDACTED}{m.group('q')}", text)
    return text


def redact_obj(obj):
    """Recursively redact every string inside a JSON-like structure."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj
