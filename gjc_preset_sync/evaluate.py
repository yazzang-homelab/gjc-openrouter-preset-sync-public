"""Explicit evaluation only. Never imported by synchronization or installation."""
from __future__ import annotations

import argparse
import datetime as dt
import json

from pathlib import Path
import time

from .core import (decode_yaml, local_model, read_bytes, read_json, validate_policy, normalize_tasks,
                   evaluation_shortlist, resolve_remote_id, catalog_models)
from . import eval_suite, quality
from .eval_state import EvalState, read_private, save_private
from .gjc_runner import GjcRunner, limits_check, file_hash


def state_root() -> Path:
    # Neither an approval nor a run/output argument can reset this root.
    return Path.home() / ".local/state/gjc-preset-sync/evaluation-state"


def execution_conditions(limits: dict, run_timeout_seconds: float) -> str:
    return quality.sha({"limits": limits, "run_timeout_seconds": run_timeout_seconds,
                       "runner_hash": file_hash(Path(__file__).with_name("gjc_runner.py")),
                       "orchestrator_hash": file_hash(Path(__file__))})


def validate_manifest(manifest: dict, policy: dict, models: dict) -> dict:
    validate_policy(policy)
    if not quality.validate_policy(policy["quality"]):
        raise quality.QualityError("policy_unconfigured")
    if manifest.get("version") != 1:
        raise quality.QualityError("Unsupported evaluation manifest")
    role, stage, selector = manifest["role"], manifest["stage"], manifest["selector"]
    if role not in quality.ROLES or stage not in ("screen", "confirm"):
        raise quality.QualityError("Invalid evaluation role or stage")
    base, local, effort = local_model(selector, models)
    allowed = [local_model(s, models)[0] for s in policy["allowed_selectors"]]
    if base not in allowed:
        raise quality.QualityError("Evaluation model is not in the exact allowlist")
    expected_effort = policy["roles"][role].get("effort") or effort
    if not expected_effort or effort != expected_effort:
        raise quality.QualityError("Manifest must pin the role's requested effort explicitly")
    if resolve_remote_id(base, local, policy, catalog_models(manifest["discovery_catalog"])) != manifest["remote_id"]:
        raise quality.QualityError("Manifest model identity differs from exact mapping")
    spec = policy["quality"]["roles"][role]
    if (spec["suite_hash"] != eval_suite.suite_hash() or spec["scorer_hash"] != eval_suite.scorer_hash()
            or spec["coverage"] != eval_suite.COVERAGE[role]):
        raise quality.QualityError("Policy does not bind the installed evaluation suite")
    cases = eval_suite.cases(role, stage)
    if manifest["cases"] != cases:
        raise quality.QualityError("Manifest must contain the entire role-stage case set")
    binding = quality.expected_binding(models, policy["quality"], role, selector, base,
                                      manifest["remote_id"], effort, stage)
    if binding != manifest["binding"]:
        raise quality.QualityError("Manifest binding differs from the current configuration")
    limits_check(manifest["limits"])
    if execution_conditions(manifest["limits"], manifest["run_timeout_seconds"]) != binding["conditions_hash"]:
        raise quality.QualityError("Execution limits differ from the observation binding")
    quality.number(manifest["run_timeout_seconds"], minimum=1)
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]:
        raise quality.QualityError("Evaluation run ID is required")
    return binding


def cached(state: EvalState, binding: dict, cases: list[str], spec: dict, *, allow_test=False) -> dict | None:
    path = state.evidence_path(quality.observation_key(binding, cases))
    if not path.exists():
        return None
    evidence = read_private(path)
    verdict = quality.assess(evidence, binding, spec, allow_test=allow_test, screen=binding["stage"] == "screen")
    return evidence if verdict["verdict"] in ("PASS", "FAIL") else None


def prepare(manifest: dict, policy: dict, models: dict, state: EvalState, *, allow_test=False) -> dict:
    binding = validate_manifest(manifest, policy, models)
    spec = policy["quality"]["roles"][binding["role"]]
    found = cached(state, binding, manifest["cases"], spec, allow_test=allow_test)
    admitted = True
    if not found:
        ranking = normalize_tasks(manifest["discovery_tasks"], policy.get("metric", "request_share"), policy.get("max_age_hours", 72))
        shortlist = evaluation_shortlist(models, policy, ranking, manifest["discovery_catalog"])
        admitted = any(row["selector"] == manifest["selector"] and row["remote_id"] == manifest["remote_id"]
                       for row in shortlist[binding["role"]])
    screen_ok = True
    if not found and binding["stage"] == "confirm":
        screen_binding = {**binding, "stage": "screen"}
        prior = cached(state, screen_binding, eval_suite.cases(binding["role"], "screen"), spec, allow_test=allow_test)
        screen_ok = bool(prior and quality.assess(prior, screen_binding, spec, allow_test=allow_test, screen=True)["verdict"] == "PASS")
    return {"status": "reused" if found else "outside_shortlist" if not admitted else "ready" if screen_ok else "screen_required",
            "observation_key": quality.observation_key(binding, manifest["cases"]),
            "new_launches": 0 if found or not screen_ok or not admitted else len(manifest["cases"]),
            "conditional_confirm_launches": len(eval_suite.cases(binding["role"], "confirm")) if binding["stage"] == "screen" and not found else 0,
            "reused": found is not None, "quota": state.preview(),
            "monetary_cap_guaranteed": False, "inference_calls": 0}


def validate_approval(approval: dict, manifest: dict) -> None:
    if (approval.get("version") != 1 or approval.get("approved") is not True
            or approval.get("acknowledge_no_monetary_cap") is not True
            or approval.get("manifest_sha256") != quality.sha(manifest)
            or not isinstance(approval.get("approval_id"), str) or not approval["approval_id"]):
        raise quality.QualityError("Missing or mismatched explicit evaluation approval")
    for key in ("selector", "role", "stage"):
        if approval.get(key) != manifest[key]:
            raise quality.QualityError("Approval scope differs from manifest")
    if approval.get("connection_revision") != manifest["binding"]["connection_revision"]:
        raise quality.QualityError("Approval connection differs")
    quality.number(approval.get("max_launches"), integer=True, minimum=1)
    if (approval["max_launches"] < len(manifest["cases"])
            or approval.get("case_timeout_seconds") != manifest["limits"]["timeout_seconds"]
            or approval.get("run_timeout_seconds") != manifest["run_timeout_seconds"]
            or quality.timestamp(approval["valid_until"]) < dt.datetime.now(dt.timezone.utc)):
        raise quality.QualityError("Evaluation approval expired or limits differ")
    if "observed_cost_soft_stop" in approval:
        stop = approval["observed_cost_soft_stop"]
        if stop.get("currency") != "USD":
            raise quality.QualityError("GJC observed cost only supports USD soft stops")
        quality.number(stop.get("amount"), minimum=0.000001)


def run(manifest: dict, approval: dict | None, policy: dict, models: dict,
        state: EvalState, runner: GjcRunner) -> tuple[dict, dict]:
    binding = validate_manifest(manifest, policy, models)
    allow_test = runner.provenance == "test"
    preview = prepare(manifest, policy, models, state, allow_test=allow_test)
    spec = policy["quality"]["roles"][binding["role"]]
    if preview["reused"]:
        return preview, cached(state, binding, manifest["cases"], spec, allow_test=allow_test)
    if preview["status"] == "screen_required":
        raise quality.QualityError("Confirmation requires valid passing screening evidence")
    if preview["status"] == "outside_shortlist":
        raise quality.QualityError("Candidate is outside the current compatible role shortlist")
    validate_approval(approval or {}, manifest)
    candidate = quality.sha([binding[k] for k in ("provider", "model", "remote_id", "connection_revision", "requested_effort")])
    reserved = state.reserve(manifest["run_id"], approval["approval_id"], preview["observation_key"], candidate,
                  binding["role"], binding["stage"], manifest["cases"], manifest["limits"]["timeout_seconds"],
                  reuse_check=lambda: cached(state, binding, manifest["cases"], spec, allow_test=allow_test),
                  manifest={"manifest": manifest, "provenance": runner.provenance})
    if not reserved:
        return {**preview, "status": "reused", "reused": True, "new_launches": 0}, cached(
            state, binding, manifest["cases"], spec, allow_test=allow_test)
    observations, started, cost = [], quality.now_iso(), 0.0
    deadline = time.monotonic() + manifest["run_timeout_seconds"]
    try:
        for case_id in manifest["cases"]:
            if time.monotonic() >= deadline:
                break
            state.start(manifest["run_id"], case_id)
            before = time.monotonic()
            case_deadline = min(deadline, before + manifest["limits"]["timeout_seconds"])
            response = ""
            observed = {}
            try:
                observed = runner.run(binding, eval_suite.prompt(case_id), timeout=case_deadline - before)
                response = observed.pop("response")
                remaining = case_deadline - time.monotonic()
                if remaining <= 0:
                    observed.update(status="timeout", trace_complete=False)
                grade_limits = {**manifest["limits"], "timeout_seconds": max(0.001, remaining)}
                passed = observed["status"] == "completed" and eval_suite.grade(case_id, response, sandbox=runner.sandbox, limits=grade_limits)
                if time.monotonic() >= case_deadline:
                    observed.update(status="timeout", trace_complete=False)
                if observed["status"] == "completed":
                    observed["status"] = "passed" if passed else "failed"
                observed.update(case_id=case_id, oracle_pass=passed)
                observations.append(observed)
            except (OSError, ValueError, KeyError, TypeError):
                observed = {**observed, "case_id": case_id, "status": "error", "oracle_pass": False,
                            "trace_complete": False, "usage_cost": observed.get("usage_cost"),
                            "identity": observed.get("identity", {})}
                observations.append(observed)
            finally:
                if observations and observations[-1].get("case_id") == case_id:
                    ref = state.record_observation(manifest["run_id"], case_id, observations[-1], response)
                    observations[-1]["artifact_ref"] = ref
                state.finish(manifest["run_id"], case_id, time.monotonic() - before)
            stop = approval.get("observed_cost_soft_stop")
            if stop:
                if observed["usage_cost"] is None:
                    break
                cost += observed["usage_cost"]
                if cost >= stop["amount"]:
                    break
            if observed["status"] in ("timeout", "substitution", "error"):
                break
    finally:
        state.cancel_unstarted(manifest["run_id"])
    for case_id in manifest["cases"][len(observations):]:
        observations.append({"case_id": case_id, "status": "missing"})
    evidence = quality.seal({"version": 1, "binding": binding, "cases": manifest["cases"],
            "observation_key": preview["observation_key"], "started_at": started, "ended_at": quality.now_iso(),
            "observations": observations, "provenance": runner.provenance,
            "budget_enforcement": "operator_approved_launch_time_limits", "monetary_cap_guaranteed": False})
    # Preserve each full run, and atomically update only the rebuildable lookup.
    state.publish(manifest["run_id"], evidence)
    verdict = quality.assess(evidence, binding, spec, allow_test=allow_test, screen=binding["stage"] == "screen")
    return {"status": "evaluated", "verdict": verdict, "launches": len([o for o in observations if o["status"] != "missing"]),
            "monetary_cap_guaranteed": False}, evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Explicit GJC evaluation; never started by synchronization")
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest, policy = read_json(args.manifest), read_json(args.policy)
        models = decode_yaml(read_bytes(args.models).decode())
        state = EvalState(state_root(), policy["quality"]["quota"])
        report = prepare(manifest, policy, models, state)
        if args.command == "run" and not report["reused"]:
            if args.approval is None:
                raise quality.QualityError("New execution requires --approval")
            approval = read_private(args.approval)
            validate_approval(approval, manifest)
            runner = GjcRunner(Path(manifest["runtime_path"]), manifest["binding"]["runtime_hash"],
                               Path(manifest["agent_dir"]), manifest["limits"])
            report, _ = run(manifest, approval, policy, models, state, runner)
        save_private(args.output / "report.json", report)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        print(json.dumps({"status": "blocked", "error": "Invalid evaluation inputs, evidence, approval, quota or runtime; no automatic retry"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
