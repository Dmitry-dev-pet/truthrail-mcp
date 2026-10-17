"""Versioned declarative capability manifest, separate from trusted scope rules.

The server NEVER imports executable Python from the manifest or accepts a URL.
The operator controls the read-only mounted release directory and active pointer.
A changed pointer is observed on the next call, with no container restart.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .identity import Denied, Principal

SHA = re.compile(r"[a-f0-9]{64}\Z")
CAP = re.compile(r"[a-z][a-z0-9_]{1,39}\Z")
VER = re.compile(r"[A-Za-z0-9._-]{1,48}\Z")
ADAPTER = re.compile(r"(?:builtin:peer_chat|delegate:[a-z][a-z0-9_]{1,39})\Z")


def strict_json(raw: bytes) -> dict:
    if len(raw) > 16384:
        raise Denied("invalid-registry")
    def no_duplicates(items):
        result = {}
        for k, v in items:
            if k in result:
                raise Denied("invalid-registry")
            result[k] = v
        return result
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Denied("invalid-registry") from exc
    if type(obj) is not dict:
        raise Denied("invalid-registry")
    return obj


@dataclass(frozen=True)
class Module:
    name: str
    version: str
    adapter: str


@dataclass(frozen=True)
class Rule:
    scope: str
    effect: str
    adapter: str
    requires_operator_approval: bool = False


class TrustedPolicy:
    """Trusted, independently reviewed policy; never loaded from a module manifest."""

    def __init__(self, rules: dict[tuple[str, str], Rule], clock):
        self.rules = dict(rules)
        self.clock = clock
        for (name, action), rule in self.rules.items():
            if not (CAP.fullmatch(name) and CAP.fullmatch(action)
                    and rule.effect in {"read", "write", "wait"}
                    and ADAPTER.fullmatch(rule.adapter)):
                raise ValueError("invalid-trusted-policy")

    def check(self, principal: Principal, module: Module, action: str,
              effect: str, resource: str | None = None) -> None:
        rule = self.rules.get((module.name, action))
        if (rule is None or rule.effect != effect
                or module.adapter != rule.adapter
                or rule.requires_operator_approval
                or not principal.allows(rule.scope, resource, self.clock())):
            raise Denied("forbidden")


class ModuleRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)

    def snapshot(self) -> tuple[str, dict[str, Module]]:
        try:
            # Both paths are fixed by operator-owned mount, not client input.
            pointer = strict_json((self.root / "active.json").read_bytes())
            if set(pointer) != {"sha256"}:
                raise Denied("invalid-registry")
            digest = pointer["sha256"]
            if type(digest) is not str or not SHA.fullmatch(digest):
                raise Denied("invalid-registry")
            raw = (self.root / "releases" / (digest + ".json")).read_bytes()
            if not __import__("hmac").compare_digest(
                    hashlib.sha256(raw).hexdigest(), digest):
                raise Denied("invalid-registry")
            manifest = strict_json(raw)
            if set(manifest) != {"schema", "modules"} or manifest["schema"] != 1:
                raise Denied("invalid-registry")
            declared = manifest["modules"]
            if type(declared) is not list or len(declared) > 32:
                raise Denied("invalid-registry")
            modules = {}
            for entry in declared:
                if type(entry) is not dict or set(entry) != {"name", "version", "adapter"}:
                    raise Denied("invalid-registry")
                name, version, adapter = entry["name"], entry["version"], entry["adapter"]
                if (type(name) is not str or not CAP.fullmatch(name)
                        or type(version) is not str or not VER.fullmatch(version)
                        or type(adapter) is not str or not ADAPTER.fullmatch(adapter)
                        or name in modules):
                    raise Denied("invalid-registry")
                modules[name] = Module(name, version, adapter)
            return digest, modules
        except (OSError, TypeError, AttributeError) as exc:
            raise Denied("invalid-registry") from exc

    def require(self, capability: str) -> Module:
        _, modules = self.snapshot()
        if capability not in modules:
            raise Denied("unknown-capability")
        return modules[capability]
