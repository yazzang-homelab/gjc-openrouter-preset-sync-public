from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .core import (SyncError, atomic_write, build_profile, decode_yaml, get_dataset,
                   initial_policy, json_bytes, mutate, normalize_spend, normalize_tasks,
                   private_dir, read_bytes, read_json, validate_policy, check_profile_quality)
from . import quality


def main(argv: list[str] | None = None) -> int:
    home = Path.home()
    agent = Path(os.environ.get("GJC_CODING_AGENT_DIR", str(home / ".gjc/agent")))
    parser = argparse.ArgumentParser(description="OpenRouter data -> GJC profiles; never calls inference")
    parser.add_argument("command", choices=["init-policy", "plan", "sync", "status", "rollback", "tasks"])
    parser.add_argument("--models", type=Path, default=agent / "models.yml")
    parser.add_argument("--policy", type=Path, default=home / ".config/gjc-preset-sync/policy.json")
    parser.add_argument("--state-dir", type=Path, default=home / ".local/state/gjc-preset-sync")
    parser.add_argument("--apply", action="store_true", help="Required for live profile writes and rollback")
    parser.add_argument("--refresh", action="store_true", help="Ignore local cache")
    parser.add_argument("--tasks-file", type=Path, help="Offline fixture / operator snapshot, never fetched")
    parser.add_argument("--catalog-file", type=Path, help="Required together with --tasks-file")
    parser.add_argument("--spend-snapshot", action="store_true", help="Explicit operator-verified spend import, not live API")
    parser.add_argument("--allow-snapshot-apply", action="store_true", help="Extra gate for applying offline/imported data")
    parser.add_argument("--bootstrap", action="store_true", help="Explicitly establish a new quality baseline; still requires all confirmations")
    parser.add_argument("--evidence-dir", type=Path, help="Private confirmation evidence directory; never executes evaluations")
    args = parser.parse_args(argv)
    try:
        def evidence_snapshot():
            directory = args.evidence_dir or args.state_dir / "evaluation-state/evidence"
            if directory.is_symlink():
                raise SyncError("Evidence directory cannot be a symlink")
            paths = sorted(directory.glob("*.json")) if directory.exists() else []
            if len(paths) > 4096:
                raise SyncError("Evidence collection exceeds its read bound")
            rows = [read_json(path) for path in paths]
            return rows, [(str(path), hashlib.sha256(read_bytes(path)).hexdigest()) for path in paths]

        def guard_for(policy, hashes, policy_hash, catalog=None):
            def guard(models, changes):
                current, current_hashes = evidence_snapshot()
                if hashlib.sha256(read_bytes(args.policy)).hexdigest() != policy_hash or hashes != current_hashes:
                    raise SyncError("Policy or evidence changed during planning")
                for profile in changes.values():
                    if profile is not None:
                        check_profile_quality(models, policy, profile, current, catalog_payload=catalog)
            return guard

        if args.command == "status":
            state_path = args.state_dir / "state.json"
            state = read_json(state_path) if state_path.exists() else {"managed": {}, "history": []}
            print(json.dumps({"managed_profiles": list(state["managed"]), "rollback_steps": len(state["history"]),
                              "pending_transaction": (args.state_dir / "pending.json").exists(),
                              "default_changed_by_tool": False, "inference_calls": 0}))
            return 0
        if args.command == "rollback":
            if not args.apply:
                raise SyncError("rollback requires --apply")
            def rollback_guard(models, changes):
                policy = read_json(args.policy)
                _, hashes = evidence_snapshot()
                guard = guard_for(policy, hashes, hashlib.sha256(read_bytes(args.policy)).hexdigest(),
                                  read_json(args.catalog_file) if args.catalog_file else None)
                guard(models, changes)
            print(json.dumps({"status": mutate(args.models, args.state_dir, rollback=True, quality_guard=rollback_guard)}))
            return 0
        original = read_bytes(args.models)
        models = decode_yaml(original.decode("utf-8"))
        if args.command == "init-policy":
            if args.policy.exists():
                raise SyncError("Policy already exists; it was not overwritten")
            policy = initial_policy(models)
            validate_policy(policy)
            private_dir(args.policy.parent)
            atomic_write(args.policy, json_bytes(policy))
            print(json.dumps({"status": "policy_created", "allowed_selectors": len(policy["allowed_selectors"]),
                              "profile_id": policy["profile_id"], "models_changed": False}))
            return 0
        policy = read_json(args.policy)
        validate_policy(policy)
        if not quality.validate_policy(policy["quality"]):
            print(json.dumps({"status": "policy_unconfigured", "inference_calls": 0, "models_changed": False}))
            return 0 if args.command == "plan" else 2
        if bool(args.tasks_file) != bool(args.catalog_file):
            raise SyncError("--tasks-file and --catalog-file must be supplied together")
        if args.spend_snapshot and not args.tasks_file:
            raise SyncError("Spend data requires an explicit operator-verified snapshot; no undocumented API is used")
        if args.tasks_file:
            payload = read_json(args.tasks_file)
            ranking = (normalize_spend(payload, policy.get("max_age_hours", 72)) if args.spend_snapshot
                       else normalize_tasks(payload, policy.get("metric", "request_share"), policy.get("max_age_hours", 72)))
            catalog, cache_used = read_json(args.catalog_file), False
        else:
            ranking, catalog, cache_used = get_dataset(policy, args.state_dir, args.refresh)
        if args.command == "tasks":
            print(json.dumps({"as_of": ranking["as_of"], "metric": ranking["metric"],
                              "tags": sorted(ranking["tasks"]), "attribution": ranking["attribution"]}))
            return 0
        rows, hashes = evidence_snapshot()
        policy_hash = hashlib.sha256(read_bytes(args.policy)).hexdigest()
        profile, report = build_profile(models, policy, ranking, catalog, rows, bootstrap=args.bootstrap)
        report["cache_used"] = cache_used
        report["source_mode"] = "operator_snapshot" if args.tasks_file else "official_data_api"
        report["status"] = "planned"
        if args.command == "sync" and args.apply:
            if args.tasks_file and not args.allow_snapshot_apply:
                raise SyncError("Snapshot application requires --allow-snapshot-apply in addition to --apply")
            report["status"] = mutate(args.models, args.state_dir, policy["profile_id"], profile,
                                      expected_sha=hashlib.sha256(original).hexdigest(),
                                      quality_guard=guard_for(policy, hashes, policy_hash, catalog))
        elif args.apply:
            raise SyncError("--apply is valid only for sync and rollback")
        private_dir(args.state_dir)
        atomic_write(args.state_dir / "last-report.json", json_bytes(report))
        print(json.dumps({"status": report["status"], "profile_id": policy["profile_id"],
                          "metric": ranking["metric"], "as_of": ranking["as_of"],
                          "model_mapping": profile["model_mapping"], "cache_used": cache_used,
                          "inference_calls": 0, "source_mode": report["source_mode"],
                          "attribution": ranking["attribution"]}, ensure_ascii=False, indent=2))
        return 0
    except SyncError as error:
        print(json.dumps({"status": "error", "error": str(error), "inference_calls": 0}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        print(json.dumps({"status": "error", "error": "Invalid input or filesystem failure; details suppressed to protect secrets"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
