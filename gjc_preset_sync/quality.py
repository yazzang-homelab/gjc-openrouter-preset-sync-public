"""Pure, configuration-bound observations. No inference or evaluator imports."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROLES = ("default", "executor", "planner", "architect", "critic")
BINDING_FIELDS = (
    "selector", "provider", "model", "remote_id", "connection_revision",
    "config_hash", "requested_effort", "runtime_hash", "runtime_version",
    "role", "stage", "suite_hash", "scorer_hash", "conditions_hash",
    "harness_hash",
)


class QualityError(ValueError):
    """Safe contract failure; never interpolate supplied values into messages."""


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def number(value: Any, *, integer: bool = False, minimum: float = 0,
           maximum: float = float("inf")) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum
            or (integer and not isinstance(value, int))):
        raise QualityError("Invalid numeric quality limit")
    return value


def timestamp(value: str) -> dt.datetime:
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise QualityError("Invalid evidence timestamp") from None


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def harness_hash() -> str:
    """Fingerprint code as data without importing or executing the evaluator."""
    root = Path(__file__).parent
    return sha({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in ("quality.py", "eval_state.py", "gjc_runner.py", "evaluate.py")})


def config_hash(models: dict, provider: str, model: str) -> str:
    """Bind the registered transport/model, not credentials or unrelated profiles."""
    entry = models.get("providers", {}).get(provider)
    if not isinstance(entry, dict):
        raise QualityError("Unregistered evidence provider")
    rows = entry.get("models", [])
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == model]
    if len(matches) != 1:
        raise QualityError("Unregistered or ambiguous evidence model")
    # Explicit transport configuration only. Rotation of secret values must not
    # trigger calls; the operator's connection_revision tracks semantic changes.
    transport = {key: entry[key] for key in ("baseUrl", "api", "compat", "requestTransform") if key in entry}
    model_config = {key: value for key, value in matches[0].items()
                    if key not in ("apiKey", "headers", "name", "cost")}
    return sha({"transport": transport, "model": model_config})


def observation_key(binding: dict, cases: list[str]) -> str:
    if not isinstance(binding, dict) or set(binding) != set(BINDING_FIELDS):
        raise QualityError("Incomplete observation binding")
    if any(not isinstance(binding[k], str) or not binding[k] for k in BINDING_FIELDS):
        raise QualityError("Invalid observation binding")
    if binding["role"] not in ROLES or binding["stage"] not in ("screen", "confirm"):
        raise QualityError("Invalid observation scope")
    if not cases or len(set(cases)) != len(cases) or any(not isinstance(c, str) or not c for c in cases):
        raise QualityError("Cases must be distinct and nonempty")
    return sha({"binding": binding, "cases": cases})


def validate_policy(quality: dict) -> bool:
    """Return False for a valid, deliberately non-executable draft."""
    if quality == {"status": "draft"}:
        return False
    if not isinstance(quality, dict) or quality.get("required_identity_level") != "gjc_reported":
        raise QualityError("Quality policy requires gjc_reported identity")
    if set(quality.get("roles", {})) != set(ROLES):
        raise QualityError("Quality policy requires all five roles")
    for role, spec in quality["roles"].items():
        if not isinstance(spec, dict):
            raise QualityError("Invalid role quality policy")
        for key in ("min_cases", "shortlist"):
            number(spec.get(key), integer=True, minimum=1, maximum=256)
        for key in ("max_failures", "max_timeouts"):
            number(spec.get(key), integer=True, maximum=256)
        number(spec.get("min_pass_rate"), maximum=1)
        for key in ("evidence_ttl_hours", "max_pair_gap_hours"):
            number(spec.get(key), minimum=0.000001)
        for key in ("suite_hash", "scorer_hash", "conditions_hash", "coverage"):
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise QualityError("Role requires suite and execution bindings")
        for key in ("confirm_cases", "screen_cases"):
            cases = spec.get(key)
            if (not isinstance(cases, list) or not cases or any(not isinstance(c, str) or not c for c in cases)
                    or len(set(cases)) != len(cases)):
                raise QualityError("Role requires exact independent stage case lists")
        pair = spec.get("paired_rule", {})
        for key in ("min_improvement", "max_failure_increase", "fallback_degradation"):
            number(pair.get(key), maximum=1)
    for key in ("runtime_hash", "runtime_version", "harness_hash"):
        if not isinstance(quality.get(key), str) or not quality[key]:
            raise QualityError("Quality policy requires runtime binding")
    connections = quality.get("connections")
    if not isinstance(connections, dict) or not connections:
        raise QualityError("Quality policy requires exact connection revisions")
    for spec in connections.values():
        if not isinstance(spec, dict) or any(not isinstance(spec.get(k), str) or not spec[k]
                                             for k in ("revision", "config_hash")):
            raise QualityError("Invalid connection revision binding")
    validate_quota(quality.get("quota", {}))
    return True


def validate_quota(limits: dict) -> None:
    if not isinstance(limits, dict):
        raise QualityError("Missing evaluation quota")
    for key in ("period_seconds", "max_launches_per_period", "max_new_candidates_per_period",
                "max_reserved_wall_seconds_per_period", "reevaluation_cooldown_seconds"):
        number(limits.get(key), integer=True, minimum=1)


def expected_binding(models: dict, quality: dict, role: str, selector: str,
                     base: str, remote_id: str, effort: str, stage: str = "confirm") -> dict:
    provider, model = base.split("/", 1)
    connection = quality["connections"].get(base)
    current_hash = config_hash(models, provider, model)
    if not connection or connection["config_hash"] != current_hash:
        raise QualityError("Current model transport differs from quality policy")
    spec = quality["roles"][role]
    if quality["harness_hash"] != harness_hash():
        raise QualityError("Evaluation implementation changed; prior evidence is not reusable")
    return {"selector": selector, "provider": provider, "model": model, "remote_id": remote_id,
            "connection_revision": connection["revision"], "config_hash": current_hash,
            "requested_effort": effort, "runtime_hash": quality["runtime_hash"],
            "runtime_version": quality["runtime_version"], "role": role, "stage": stage,
            "suite_hash": spec["suite_hash"], "scorer_hash": spec["scorer_hash"],
            "conditions_hash": spec["conditions_hash"], "harness_hash": quality["harness_hash"]}


def seal(evidence: dict) -> dict:
    body = {k: v for k, v in evidence.items() if k != "sha256"}
    return {**body, "sha256": sha(body)}


def assess(evidence: dict, expected: dict, spec: dict, *, now: dt.datetime | None = None,
           allow_test: bool = False, screen: bool = False) -> dict:
    """Recompute a verdict from complete observations, never trust a stored score."""
    result = {"verdict": "UNKNOWN", "reason": "invalid_evidence", "passed": 0,
              "failures": 0, "timeouts": 0, "n": 0, "rate": 0.0}
    try:
        if not isinstance(evidence, dict) or evidence.get("sha256") != seal(evidence)["sha256"]:
            return result
        if evidence.get("version") != 1 or evidence.get("binding") != expected:
            return result
        if evidence.get("provenance") != "live" and not (allow_test and evidence.get("provenance") == "test"):
            return result
        if expected["stage"] != ("screen" if screen else "confirm"):
            return result
        cases = evidence["cases"]
        if cases != spec["screen_cases" if screen else "confirm_cases"]:
            return result
        if observation_key(expected, cases) != evidence.get("observation_key"):
            return result
        start, end = timestamp(evidence["started_at"]), timestamp(evidence["ended_at"])
        current = now or dt.datetime.now(dt.timezone.utc)
        if not start <= end <= current or (current - start).total_seconds() > spec["evidence_ttl_hours"] * 3600:
            return {**result, "reason": "stale_or_future"}
        slots = evidence["observations"]
        if not isinstance(slots, list) or [s.get("case_id") for s in slots] != cases:
            return result
        result["n"] = len(cases)
        unknown = False
        for slot in slots:
            status = slot.get("status")
            if status not in ("passed", "failed", "timeout", "substitution", "error", "missing"):
                return result
            identity = slot.get("identity", {})
            if status == "missing":
                unknown = True
                continue
            if status in ("error", "timeout") or (status != "substitution" and not slot.get("trace_complete")):
                unknown = True
            if identity.get("provider") != expected["provider"] or identity.get("model") != expected["model"]:
                if status != "substitution":
                    unknown = True
            if (identity.get("effective_effort") is not None or identity.get("backend_revision") is not None
                    or identity.get("effort_attestation") != "requested_only"):
                return result
            if status == "passed":
                if not slot.get("trace_complete") or slot.get("oracle_pass") is not True:
                    unknown = True
                else:
                    result["passed"] += 1
            elif status == "timeout":
                result["timeouts"] += 1
                result["failures"] += 1
            else:
                result["failures"] += 1
        result["rate"] = result["passed"] / result["n"]
        if unknown or (not screen and result["n"] < spec["min_cases"]):
            return {**result, "reason": "incomplete_or_insufficient"}
        passed = (result["passed"] == result["n"] if screen else
                  result["failures"] <= spec["max_failures"] and result["timeouts"] <= spec["max_timeouts"]
                  and result["rate"] >= spec["min_pass_rate"])
        # A contradictory model attribution is never policy-tolerated.
        if any(s["status"] == "substitution" for s in slots):
            passed = False
        return {**result, "verdict": "PASS" if passed else "FAIL", "reason": "policy"}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return result


def paired(candidate: dict, incumbent: dict, spec: dict, *, fallback: bool = False,
           now: dt.datetime | None = None, allow_test: bool = False) -> bool:
    a, b = candidate.get("binding", {}), incumbent.get("binding", {})
    common = ("role", "stage", "runtime_hash", "runtime_version", "suite_hash", "scorer_hash", "conditions_hash", "harness_hash")
    if any(a.get(k) != b.get(k) for k in common) or candidate.get("cases") != incumbent.get("cases"):
        return False
    ca = assess(candidate, a, spec, now=now, allow_test=allow_test)
    cb = assess(incumbent, b, spec, now=now, allow_test=allow_test)
    if ca["verdict"] != "PASS" or cb["verdict"] != "PASS":
        return False
    if abs((timestamp(candidate["started_at"]) - timestamp(incumbent["started_at"])).total_seconds()) > spec["max_pair_gap_hours"] * 3600:
        return False
    rule = spec["paired_rule"]
    improvement = ca["rate"] - cb["rate"]
    allowed = -rule["fallback_degradation"] if fallback else rule["min_improvement"]
    return (improvement >= allowed and (fallback or improvement > 0)
            and (ca["failures"] - cb["failures"]) / ca["n"] <= rule["max_failure_increase"])
