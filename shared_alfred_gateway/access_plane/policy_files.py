"""Independent operator-owned policy snapshots: NOT writable by capabilities.

Policy and module directories must be independently mounted read-only to the
application UID. Deployment control plane, not an MCP tool, owns their atomically
switched pointers. These files do not accept arbitrary module source or URLs.
"""
from __future__ import annotations

import hashlib
from hmac import compare_digest
from pathlib import Path

from .identity import Denied
from .registry import ADAPTER, CAP, Rule, SHA, TrustedPolicy, strict_json

class TrustedPolicyFiles:
    def __init__(self, root: Path, clock):
        self.root = Path(root)
        self.clock = clock

    def __call__(self) -> TrustedPolicy:
        try:
            pointer = strict_json((self.root / "active.json").read_bytes())
            if set(pointer) != {"sha256"}:
                raise Denied("invalid-policy")
            digest = pointer["sha256"]
            if type(digest) is not str or not SHA.fullmatch(digest):
                raise Denied("invalid-policy")
            raw = (self.root / "releases" / (digest + ".json")).read_bytes()
            if not compare_digest(hashlib.sha256(raw).hexdigest(), digest):
                raise Denied("invalid-policy")
            manifest = strict_json(raw)
            if set(manifest) != {"schema", "rules"} or manifest["schema"] != 1:
                raise Denied("invalid-policy")
            entries = manifest["rules"]
            if type(entries) is not list or len(entries) > 128:
                raise Denied("invalid-policy")
            rules = {}
            for entry in entries:
                if type(entry) is not dict or set(entry) != {
                    "capability", "action", "scope", "effect",
                    "adapter", "operator_gate"
                }:
                    raise Denied("invalid-policy")
                name, action = entry["capability"], entry["action"]
                if (type(name) is not str or CAP.fullmatch(name) is None
                        or type(action) is not str or CAP.fullmatch(action) is None
                        or type(entry["scope"]) is not str
                        or type(entry["effect"]) is not str
                        or type(entry["adapter"]) is not str
                        or ADAPTER.fullmatch(entry["adapter"]) is None
                        or type(entry["operator_gate"]) is not bool
                        or (name, action) in rules):
                    raise Denied("invalid-policy")
                rules[name, action] = Rule(
                    entry["scope"], entry["effect"], entry["adapter"],
                    entry["operator_gate"]
                )
            return TrustedPolicy(rules, self.clock)
        except (OSError, TypeError, AttributeError, ValueError) as exc:
            raise Denied("invalid-policy") from exc
