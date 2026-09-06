"""Synthetic observations and reference artifacts; never shipped as live evidence."""
import difflib
import json


from gjc_preset_sync import core, quality, eval_suite
from gjc_preset_sync.gjc_runner import execute

LIMITS = {"timeout_seconds": 10, "memory_bytes": 1024 * 1024 * 1024,
          "cpu_seconds": 10, "processes": 64, "file_bytes": 1024 * 1024, "output_bytes": 1024 * 1024}
QUOTA = {"period_seconds": 86400, "max_launches_per_period": 100,
         "max_new_candidates_per_period": 10, "max_reserved_wall_seconds_per_period": 2000,
         "reevaluation_cooldown_seconds": 30}


def configure(models, policy):
    from gjc_preset_sync.evaluate import execution_conditions
    connections = {}
    for selector in policy["allowed_selectors"]:
        base, local, _ = core.local_model(selector, models)
        provider, model = base.split("/", 1)
        connections[base] = {"revision": "test-connection", "config_hash": quality.config_hash(models, provider, model)}
    policy["quality"] = {"required_identity_level": "gjc_reported", "runtime_hash": "test-runtime",
        "harness_hash": quality.harness_hash(),
        "runtime_version": "test-v1", "connections": connections, "quota": dict(QUOTA), "roles": {
            role: {"min_cases": len(eval_suite.cases(role, "confirm")), "max_failures": 0, "max_timeouts": 0,
                   "min_pass_rate": 1, "shortlist": 2, "evidence_ttl_hours": 24, "max_pair_gap_hours": 24,
                   "suite_hash": eval_suite.suite_hash(), "scorer_hash": eval_suite.scorer_hash(),
                   "conditions_hash": execution_conditions(LIMITS, 60), "coverage": eval_suite.COVERAGE[role],
                   "confirm_cases": eval_suite.cases(role, "confirm"), "screen_cases": eval_suite.cases(role, "screen"),
                   "paired_rule": {"min_improvement": 0, "max_failure_increase": 0, "fallback_degradation": 0}}
            for role in quality.ROLES}}
    return policy


def evidence_rows(models, policy, *, provenance="test"):
    result = []
    for role in quality.ROLES:
        for selector in policy["allowed_selectors"]:
            base, local, effort = core.local_model(selector, models)
            effort = policy["roles"][role].get("effort") or effort
            if not effort:
                continue
            selector = base + ":" + effort
            remote = policy.get("aliases", {}).get(base, "vendor/" + local["id"])
            try:
                binding = quality.expected_binding(models, policy["quality"], role, selector, base, remote, effort)
            except quality.QualityError:
                continue
            ids = eval_suite.cases(role, "confirm")
            observations = [{"case_id": case_id, "status": "passed", "trace_complete": True, "oracle_pass": True,
                    "identity": {"provider": binding["provider"], "model": binding["model"],
                                 "effective_effort": None, "backend_revision": None, "effort_attestation": "requested_only"}}
                    for case_id in ids]
            result.append(quality.seal({"version": 1, "binding": binding, "cases": ids,
                   "observation_key": quality.observation_key(binding, ids), "started_at": quality.now_iso(),
                   "ended_at": quality.now_iso(), "observations": observations, "provenance": provenance}))
    return result


SOLUTIONS = {
    "executor-parser": {"solution.py": "def parse_csv(text):\n    return [s.strip() for s in text.split(',')] if text.strip() else []\n"},
    "executor-transaction": {"solution.py": "def transfer(state, tx, amount):\n    if tx in state['seen']:\n        return state['balance']\n    if amount < 0 or amount > state['balance']:\n        raise ValueError()\n    state['balance'] -= amount\n    state['seen'].add(tx)\n    return state['balance']\n"},
    "architect-isolation": {"solution.py": "def isolate(functions):\n    results = []\n    for f in functions:\n        try:\n            results.append(f())\n        except Exception:\n            results.append(None)\n    return results\n"},
    "architect-coupling": {"domain.py": "def price(qty, unit):\n    return qty * unit\n"},
    "executor-screen": {"solution.py": "def inc(n):\n    return n + 1\n"},
    "architect-screen": {"solution.py": "def ensure(fetch, fallback):\n    try:\n        return fetch()\n    except Exception:\n        return fallback\n"},
}
ANSWERS = {"default-transform": {"a": 7, "b": 5, "c": 0}, "default-intervals": [[1, 4], [5, 9], [10, 10]],
           "default-screen": [-3, 9], "critic-boundary": {"defect": True, "line": 2, "input": [3, 3], "expected": 3},
           "critic-order": {"defect": True, "line": 2, "input": [2, 1, 2], "expected": [2, 1]},
           "critic-clean": {"defect": False}, "critic-screen": {"defect": True, "line": 2, "input": [], "expected": None},
           "planner-recovery": ["stop_b", "stop_a", "repair_storage", "start_a", "start_b"],
           "planner-migration": ["snapshot", "disable_writes", "copy", "verify", "switch", "enable_writes"],
           "planner-screen": ["unlock", "open"]}


def reference(case_id):
    if case_id in ANSWERS:
        return json.dumps(ANSWERS[case_id])
    original = eval_suite.CATALOG[case_id]["files"]
    return "".join("".join(difflib.unified_diff(original[name].splitlines(True), text.splitlines(True),
                   fromfile="a/" + name, tofile="b/" + name)) for name, text in SOLUTIONS[case_id].items())


class TrustedSandbox:
    """Test-only mapping for trusted synthetic executable and reference solutions."""
    def run(self, argv, work, limits, *, network=False, mounts=(), env=None):
        mapping = {target: str(source) for source, target in mounts}
        mapped = []
        for item in argv:
            for target, source in mapping.items():
                if item == target or item.startswith(target + "/"):
                    item = source + item[len(target):]
                    break
            mapped.append(item)
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(work), "LANG": "C.UTF-8", "PYTHONPATH": str(work)}
        return execute(mapped, work, environment, limits)
