"""Protocol v1: JSON Lines envelopes, error codes and parameter validation.

contracts/protocol-v1.md is the authoritative description. Keep it in sync.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024  # one JSON line, either direction
MAX_ID_LEN = 128
METHOD_RE = re.compile(r"^[a-z]+(\.[a-z_]+)+$")


class ErrorCode:
    PARSE_ERROR = "PARSE_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE"
    UNKNOWN_METHOD = "UNKNOWN_METHOD"
    INVALID_PARAMS = "INVALID_PARAMS"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    NO_ACTIVE_PROJECT = "NO_ACTIVE_PROJECT"
    INVALID_REPOSITORY = "INVALID_REPOSITORY"
    PATH_REJECTED = "PATH_REJECTED"
    GRAPH_UNAVAILABLE = "GRAPH_UNAVAILABLE"
    GRAPH_INVALID = "GRAPH_INVALID"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    PLAN_INVALID = "PLAN_INVALID"
    WORKFLOW_INVALID = "WORKFLOW_INVALID"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ILLEGAL_STATE = "ILLEGAL_STATE"
    BUSY = "BUSY"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL = "INTERNAL"


ALL_ERROR_CODES = sorted(v for k, v in vars(ErrorCode).items() if k.isupper())


class BrainError(Exception):
    """An error that is reported to the client as a protocol error object."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class Request:
    id: str
    method: str
    params: dict


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode()


def ok(req_id: str, result: Any) -> dict:
    return {"v": PROTOCOL_VERSION, "id": req_id, "ok": True, "result": result}


def err(req_id: str | None, error: BrainError) -> dict:
    return {"v": PROTOCOL_VERSION, "id": req_id, "ok": False, "error": error.to_dict()}


def event(name: str, data: dict) -> dict:
    return {"v": PROTOCOL_VERSION, "event": name, "data": data}


def parse_request(line: bytes) -> Request:
    """Parse one line into a Request or raise BrainError.

    On failure the error carries ``details.id`` when the id could be
    recovered, so the client can still correlate the rejection.
    """
    if len(line) > MAX_MESSAGE_BYTES:
        raise BrainError(ErrorCode.MESSAGE_TOO_LARGE, f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    try:
        obj = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrainError(ErrorCode.PARSE_ERROR, f"invalid JSON: {exc.msg if hasattr(exc, 'msg') else exc}")
    if not isinstance(obj, dict):
        raise BrainError(ErrorCode.INVALID_REQUEST, "request must be a JSON object")
    req_id = obj.get("id")
    if not isinstance(req_id, str) or not req_id or len(req_id) > MAX_ID_LEN:
        raise BrainError(ErrorCode.INVALID_REQUEST, f"'id' must be a non-empty string of at most {MAX_ID_LEN} chars")
    rid = {"id": req_id}
    if obj.get("v") != PROTOCOL_VERSION or isinstance(obj.get("v"), bool):
        raise BrainError(ErrorCode.UNSUPPORTED_VERSION, f"unsupported protocol version {obj.get('v')!r}; server speaks v{PROTOCOL_VERSION}", rid)
    extra = obj.keys() - {"v", "id", "method", "params"}
    if extra:
        raise BrainError(ErrorCode.INVALID_REQUEST, f"unknown envelope keys: {sorted(extra)}", rid)
    method = obj.get("method")
    if not isinstance(method, str) or not METHOD_RE.match(method) or len(method) > 64:
        raise BrainError(ErrorCode.INVALID_REQUEST, "'method' must look like 'namespace.name'", rid)
    params = obj.get("params", {})
    if not isinstance(params, dict):
        raise BrainError(ErrorCode.INVALID_REQUEST, "'params' must be an object", rid)
    return Request(req_id, method, params)


# ---------------------------------------------------------------------------
# Parameter validation


@dataclass
class P:
    """Declarative parameter spec."""

    type: type | tuple
    required: bool = False
    default: Any = None
    max_len: int | None = None
    min: int | None = None
    max: int | None = None
    choices: tuple | None = None
    item_type: type | None = None
    max_items: int | None = None
    check: Callable[[Any], str | None] | None = None


def validate_params(spec: dict[str, P], params: dict) -> dict:
    unknown = params.keys() - spec.keys()
    if unknown:
        raise BrainError(ErrorCode.INVALID_PARAMS, f"unknown parameter(s): {sorted(unknown)}")
    out: dict = {}
    for name, p in spec.items():
        if name not in params or params[name] is None:
            if p.required:
                raise BrainError(ErrorCode.INVALID_PARAMS, f"missing required parameter '{name}'")
            out[name] = p.default
            continue
        value = params[name]
        types = p.type if isinstance(p.type, tuple) else (p.type,)
        # bool is a subclass of int; never let True pass as an integer.
        if isinstance(value, bool) and bool not in types:
            raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' has the wrong type")
        if not isinstance(value, types):
            names = "/".join(t.__name__ for t in types)
            raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' must be {names}")
        if isinstance(value, str):
            if p.max_len is not None and len(value) > p.max_len:
                raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' longer than {p.max_len} chars")
            if "\x00" in value:
                raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' contains a NUL byte")
        if isinstance(value, int) and not isinstance(value, bool):
            if p.min is not None and value < p.min or p.max is not None and value > p.max:
                raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' must be between {p.min} and {p.max}")
        if p.choices is not None and value not in p.choices:
            raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' must be one of {list(p.choices)}")
        if isinstance(value, list):
            if p.max_items is not None and len(value) > p.max_items:
                raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' has more than {p.max_items} items")
            if p.item_type and not all(isinstance(v, p.item_type) and not isinstance(v, bool) for v in value):
                raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}' items must be {p.item_type.__name__}")
        if p.check:
            problem = p.check(value)
            if problem:
                raise BrainError(ErrorCode.INVALID_PARAMS, f"'{name}': {problem}")
        out[name] = value
    return out
