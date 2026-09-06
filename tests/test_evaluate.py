import copy
import datetime as dt
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gjc_preset_sync import eval_suite, evaluate, quality, core
from gjc_preset_sync.eval_state import EvalState
from gjc_preset_sync.gjc_runner import GjcRunner, file_hash, parse_events, execute, Sandbox
from eval_helpers import LIMITS, QUOTA, TrustedSandbox, reference
from test_sync import fixture


def fake_runtime(path, *, wrong=False):
    responses = {eval_suite.prompt(case_id): reference(case_id) for case_id in eval_suite.CATALOG}
    path.write_text("#!/usr/bin/python3\nimport json,sys\nresponses=" + repr(responses) + "\n"
        "prompt=sys.argv[-1].split('\\n',1)[1]\n"
        "message={'role':'assistant','provider':'local','model':" + repr("wrong" if wrong else "alpha") + ","
        "'stopReason':'stop','usage':{'cost':{'total':0}},'content':[{'type':'text','text':responses[prompt]}]}\n"
        "print(json.dumps({'type':'agent_start'}))\n"
        "print(json.dumps({'type':'turn_start'}))\n"
        "print(json.dumps({'type':'message_start','message':message}))\n"
        "print(json.dumps({'type':'message_end','message':message}))\n"
        "print(json.dumps({'type':'turn_end','message':message,'toolResults':[]}))\n"
        "print(json.dumps({'type':'agent_end','messages':[message]}))\n")
    path.chmod(0o700)


class SuiteTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bwrap") and os.environ.get("GJC_TEST_RUNTIME"), "Explicit offline GJC runtime probe not configured")
    def test_existing_gjc_binary_starts_without_network_or_credentials(self):
        runtime = Path(os.environ["GJC_TEST_RUNTIME"])
        self.assertEqual(runtime.read_bytes()[:4], b"\x7fELF")
        with tempfile.TemporaryDirectory() as root:
            result = Sandbox().run(["/runtime/gjc", "--version"], Path(root),
                    {**LIMITS, "memory_bytes": 16 * 1024 ** 3}, mounts=[(runtime, "/runtime/gjc")], network=False)
        self.assertEqual(result["exit_code"], 0, result["stderr"])
        self.assertTrue(result["stdout"].startswith(b"gjc/"))

    @unittest.skipUnless(shutil.which("bwrap"), "Live bubblewrap isolation unavailable; not a live PASS")
    def test_live_networkless_patch_grading_and_host_secret_isolation(self):
        boundary = Sandbox()
        for key, case in eval_suite.CATALOG.items():
            if case["kind"] == "patch":
                self.assertTrue(eval_suite.grade(key, reference(key), sandbox=boundary, limits=LIMITS), key)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            secret = path / "not-mounted"
            secret.write_text("synthetic-private-marker")
            work = path / "work"
            work.mkdir()
            result = boundary.run(["/usr/bin/python3", "-c",
                "from pathlib import Path; assert not Path(" + repr(str(secret)) + ").exists(); print('isolated')"], work, LIMITS)
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], b"isolated\n")

    def test_all_sixteen_real_oracles_accept_reference_reject_empty_and_parrot(self):
        self.assertEqual(len(eval_suite.CATALOG), 16)
        for key in eval_suite.CATALOG:
            with self.subTest(case=key):
                self.assertTrue(eval_suite.grade(key, reference(key), sandbox=TrustedSandbox(), limits=LIMITS))
                self.assertFalse(eval_suite.grade(key, "", sandbox=TrustedSandbox(), limits=LIMITS))
                self.assertFalse(eval_suite.grade(key, json.dumps(key), sandbox=TrustedSandbox(), limits=LIMITS))

    def test_patch_escape_oracle_tamper_and_exit_bypass(self):
        base = reference("executor-screen")
        for bad in (base.replace("solution.py", "../solution.py"), base.replace("solution.py", "check.py"),
                    base.replace("+    return n + 1", "+    raise SystemExit(0)"),
                    base.replace("+    return n + 1", "+    return n")):
            self.assertFalse(eval_suite.grade("executor-screen", bad, sandbox=TrustedSandbox(), limits=LIMITS))

    def test_reproduction_and_plan_invariant_not_keyword_grading(self):
        self.assertFalse(eval_suite.grade("critic-boundary", json.dumps({"defect": True, "line": 2, "input": [1, 3], "expected": 1})))
        self.assertFalse(eval_suite.grade("critic-clean", json.dumps({"defect": True})))
        self.assertFalse(eval_suite.grade("planner-migration", json.dumps(["snapshot", "copy", "verify", "switch", "enable_writes"])))
        self.assertFalse(eval_suite.grade("planner-recovery", json.dumps(["repair_storage", "start_a", "start_b"])))
        self.assertFalse(eval_suite.grade("default-transform", '{"a":7,"b":5,"c":false}'))


class RunnerTests(unittest.TestCase):
    def test_late_error_partial_substitution_and_unknown_cost(self):
        message = {"role": "assistant", "provider": "p", "model": "m", "stopReason": "stop", "content": [{"type": "text", "text": "ok"}]}
        events = [{"type": "agent_start"}, {"type": "turn_start"}, {"type": "message_start", "message": message},
                  {"type": "message_end", "message": message}, {"type": "turn_end", "message": message, "toolResults": []},
                  {"type": "agent_end", "messages": [message]}]
        def result(rows):
            return {"stdout": "\n".join(map(json.dumps, rows)).encode(), "elapsed": 0, "status": "exited", "exit_code": 0}
        self.assertEqual(parse_events(result(events), "p", "m")["status"], "completed")
        self.assertIsNone(parse_events(result(events), "p", "m")["usage_cost"])
        self.assertEqual(parse_events(result(events + [{"type": "agent_failed"}]), "p", "m")["status"], "error")
        self.assertEqual(parse_events(result(events[:-1]), "p", "m")["status"], "error")
        self.assertEqual(parse_events(result(events), "p", "other")["status"], "substitution")
        inconsistent = copy.deepcopy(events)
        inconsistent[-1]["messages"][0] = {**message, "model": "other"}
        self.assertEqual(parse_events(result(inconsistent), "p", "m")["status"], "substitution")
        for invalid in (events + [{"type": "message_end", "message": message}],
                        events[:-1] + [{"type": "new_error"}] + events[-1:],
                        events[-1:] + events[:-1], events[:-1] + [{}] + events[-1:]):
            self.assertEqual(parse_events(result(invalid), "p", "m")["status"], "error")

    def test_timeout_output_cap_and_no_implicit_sandbox_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            result = execute(["/usr/bin/python3", "-c", "while True: pass"], p, {"PATH": "/usr/bin"}, {**LIMITS, "timeout_seconds": 0.1})
            self.assertEqual(result["status"], "timeout")
            result = execute(["/usr/bin/python3", "-c", "print('x'*10000)"], p, {}, {**LIMITS, "output_bytes": 100})
            self.assertEqual(result["status"], "oversize")
        with patch("gjc_preset_sync.gjc_runner.shutil.which", return_value=None):
            with self.assertRaises(quality.QualityError):
                Sandbox()


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.models, self.policy, self.catalog, self.data = fixture()
        self.policy["aliases"] = {"local/alpha": "vendor/alpha", "local/beta": "vendor/beta"}
        self.runtime = self.root / "fake-gjc"
        fake_runtime(self.runtime)
        self.agent = self.root / "agent"
        self.agent.mkdir()
        self.policy["quality"]["runtime_hash"] = file_hash(self.runtime)
        self.runner = GjcRunner(self.runtime, file_hash(self.runtime), self.agent, LIMITS, _test_sandbox=TrustedSandbox())
        self.state = EvalState(self.root / "state", QUOTA)
        self.count = 0

    def manifest(self, role, stage):
        self.count += 1
        binding = quality.expected_binding(self.models, self.policy["quality"], role, "local/alpha:high", "local/alpha", "vendor/alpha", "high", stage)
        return {"version": 1, "run_id": str(self.count), "role": role, "stage": stage, "selector": "local/alpha:high",
                "remote_id": "vendor/alpha", "binding": binding, "cases": eval_suite.cases(role, stage),
                "limits": dict(LIMITS), "run_timeout_seconds": 60,
                "discovery_tasks": self.data, "discovery_catalog": self.catalog}

    def approval(self, manifest):
        return {"version": 1, "approval_id": manifest["run_id"], "manifest_sha256": quality.sha(manifest),
                "selector": manifest["selector"], "role": manifest["role"], "stage": manifest["stage"],
                "connection_revision": manifest["binding"]["connection_revision"], "max_launches": len(manifest["cases"]),
                "case_timeout_seconds": 10, "run_timeout_seconds": 60,
                "valid_until": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
                "approved": True, "acknowledge_no_monetary_cap": True}

    def test_full_real_fake_subprocess_sixteen_cases_all_roles_apply_and_reuse(self):
        self.policy["aliases"] = {}
        evidence = []
        for role in quality.ROLES:
            for stage in ("screen", "confirm"):
                manifest = self.manifest(role, stage)
                report, row = evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, self.runner)
                self.assertEqual(report["verdict"]["verdict"], "PASS", (role, stage, row))
                if stage == "confirm":
                    evidence.append(row)
                with patch.object(self.runner, "run", side_effect=AssertionError("must reuse")):
                    reused, _ = evaluate.run(manifest, None, self.policy, self.models, self.state, self.runner)
                self.assertEqual(reused["new_launches"], 0)
        profile, _ = core.build_profile(self.models, self.policy, core.normalize_tasks(self.data, "request_share"), self.catalog,
                                        evidence, bootstrap=True, allow_test=True)
        path = self.root / "models.yml"
        core.atomic_write(path, core.yaml.safe_dump(self.models).encode())
        self.assertEqual(core.mutate(path, self.root / "sync", "or-auto", profile,
            quality_guard=lambda models, changes: core.check_profile_quality(models, self.policy, changes["or-auto"], evidence,
                                                                           allow_test=True, catalog_payload=self.catalog)), "applied")
        self.assertEqual(set(core.decode_yaml(path.read_text())["profiles"]["or-auto"]["model_mapping"]), set(quality.ROLES))
        with self.assertRaises(core.SyncError):
            core.check_profile_quality(self.models, self.policy, profile, evidence, catalog_payload=self.catalog)

    def test_unapproved_partial_manifest_and_screen_requirement(self):
        manifest = self.manifest("default", "screen")
        with self.assertRaises(quality.QualityError):
            evaluate.run(manifest, None, self.policy, self.models, self.state, self.runner)
        manifest = self.manifest("default", "confirm")
        self.assertEqual(evaluate.prepare(manifest, self.policy, self.models, self.state, allow_test=True)["new_launches"], 0)
        with self.assertRaises(quality.QualityError):
            evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, self.runner)
        manifest["cases"] = manifest["cases"][:1]
        with self.assertRaises(quality.QualityError):
            evaluate.validate_manifest(manifest, self.policy, self.models)

    def test_substitution_screen_stops_confirmation_without_new_launches(self):
        fake_runtime(self.runtime, wrong=True)
        self.policy["quality"]["runtime_hash"] = file_hash(self.runtime)
        runner = GjcRunner(self.runtime, file_hash(self.runtime), self.agent, LIMITS, _test_sandbox=TrustedSandbox())
        manifest = self.manifest("default", "screen")
        report, row = evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, runner)
        self.assertEqual(report["verdict"]["verdict"], "FAIL")
        manifest = self.manifest("default", "confirm")
        with patch.object(runner, "run", side_effect=AssertionError("must not launch")):
            self.assertEqual(evaluate.prepare(manifest, self.policy, self.models, self.state, allow_test=True)["new_launches"], 0)
            with self.assertRaises(quality.QualityError):
                evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, runner)

    def test_shortlist_limit_rejects_new_candidate_before_spawning(self):
        self.policy["quality"]["roles"]["default"]["shortlist"] = 1
        manifest = self.manifest("default", "screen")
        with patch.object(self.runner, "run", side_effect=AssertionError("outside shortlist")):
            report = evaluate.prepare(manifest, self.policy, self.models, self.state, allow_test=True)
            self.assertEqual(report["status"], "outside_shortlist")
            self.assertEqual(report["new_launches"], 0)
            with self.assertRaises(quality.QualityError):
                evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, self.runner)

    def test_unique_catalog_mapping_without_alias_works_end_to_end(self):
        self.policy["aliases"] = {}
        manifest = self.manifest("default", "screen")
        report, _ = evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, self.runner)
        self.assertEqual(report["verdict"]["verdict"], "PASS")

    def test_changed_run_deadline_or_harness_invalidates_binding(self):
        manifest = self.manifest("default", "screen")
        manifest["run_timeout_seconds"] = 61
        with self.assertRaises(quality.QualityError):
            evaluate.validate_manifest(manifest, self.policy, self.models)
        with patch.object(quality, "harness_hash", return_value="different-implementation"):
            with self.assertRaises(quality.QualityError):
                self.manifest("default", "screen")

    def test_model_and_grader_share_deadline_and_observations_are_durable(self):
        manifest = self.manifest("default", "screen")
        clock = [0.0]
        observed = {"status": "completed", "trace_complete": True, "response": "[-3,9]", "usage_cost": 0,
                    "identity": {"provider": "local", "model": "alpha", "effective_effort": None,
                                 "backend_revision": None, "effort_attestation": "requested_only"}}
        def model(*args, **kwargs):
            clock[0] += 8
            return copy.deepcopy(observed)
        def grader(*args, **kwargs):
            self.assertEqual(kwargs["limits"]["timeout_seconds"], 2)
            clock[0] += 1
            return True
        with patch.object(evaluate.time, "monotonic", side_effect=lambda: clock[0]), patch.object(self.runner, "run", side_effect=model), patch.object(eval_suite, "grade", side_effect=grader):
            report, row = evaluate.run(manifest, self.approval(manifest), self.policy, self.models, self.state, self.runner)
        self.assertEqual(report["verdict"]["verdict"], "PASS")
        private = self.state.root / "runs" / quality.sha(manifest["run_id"])
        self.assertTrue((private / "manifest.json").exists())
        record = json.loads((private / (quality.sha(manifest["cases"][0]) + ".json")).read_text())
        self.assertEqual(record["artifact"], "[-3,9]")

    def test_interrupted_run_recovers_full_denominator_without_budget_refund(self):
        manifest = self.manifest("default", "confirm")
        key = quality.observation_key(manifest["binding"], manifest["cases"])
        self.state.reserve("crash", "crash", key, "candidate", "default", "confirm", manifest["cases"], 10,
                           manifest={"manifest": manifest, "provenance": "test"})
        self.state.start("crash", manifest["cases"][0])
        before = self.state.preview()["usage"]
        recovered = self.state.recover_evidence("crash")
        self.assertEqual(len(recovered["observations"]), 2)
        self.assertEqual(quality.assess(recovered, manifest["binding"], self.policy["quality"]["roles"]["default"], allow_test=True)["verdict"], "UNKNOWN")
        self.assertEqual(self.state.preview()["usage"], before)

    def test_grader_infrastructure_failure_cannot_pass_permissive_policy(self):
        self.policy["quality"]["roles"]["executor"].update(max_failures=1, min_pass_rate=0.5)
        screen = self.manifest("executor", "screen")
        evaluate.run(screen, self.approval(screen), self.policy, self.models, self.state, self.runner)
        class BrokenSecondGrader(TrustedSandbox):
            graded = 0
            def run(self, argv, work, limits, *, network=False, mounts=(), env=None):
                if not network:
                    self.graded += 1
                    if self.graded == 2:
                        return {"status": "exited", "exit_code": 1, "stdout": b"", "stderr": b"synthetic startup failure", "elapsed": 0}
                return super().run(argv, work, limits, network=network, mounts=mounts, env=env)
        self.runner.sandbox = BrokenSecondGrader()
        confirm = self.manifest("executor", "confirm")
        report, evidence = evaluate.run(confirm, self.approval(confirm), self.policy, self.models, self.state, self.runner)
        self.assertEqual(report["verdict"]["verdict"], "UNKNOWN")
        self.assertEqual((report["verdict"]["n"], report["verdict"]["passed"]), (2, 1))
        self.assertEqual(evidence["observations"][1]["status"], "error")
        self.assertTrue(evidence["observations"][1]["raw_trace"])
