import copy
import datetime as dt
import unittest

from gjc_preset_sync import quality as q, core
from eval_helpers import evidence_rows
from test_sync import fixture


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.models, self.policy, self.catalog, self.data = fixture()
        self.row = evidence_rows(self.models, self.policy)[0]
        self.binding = self.row["binding"]
        self.spec = self.policy["quality"]["roles"][self.binding["role"]]

    def assess(self, row=None, **kw):
        return q.assess(row or self.row, self.binding, self.spec, allow_test=True, **kw)

    def test_requested_effort_null_effective_is_supported(self):
        self.assertEqual(self.assess()["verdict"], "PASS")
        self.assertEqual(q.assess(self.row, self.binding, self.spec)["verdict"], "UNKNOWN")

    def test_tampering_and_backend_claim_are_rejected(self):
        bad = copy.deepcopy(self.row)
        bad["observations"][0]["status"] = "failed"
        self.assertEqual(self.assess(bad)["verdict"], "UNKNOWN")
        bad = q.seal(bad)
        self.assertEqual(self.assess(bad)["verdict"], "FAIL")
        bad["observations"][0]["identity"]["effective_effort"] = "high"
        self.assertEqual(self.assess(q.seal(bad))["verdict"], "UNKNOWN")

    def test_failure_missing_and_timeouts_stay_in_denominator(self):
        row = copy.deepcopy(self.row)
        row["observations"][0]["status"] = "timeout"
        verdict = self.assess(q.seal(row))
        self.assertEqual((verdict["verdict"], verdict["n"], verdict["timeouts"]), ("UNKNOWN", 2, 1))
        row["observations"][0] = {"case_id": row["cases"][0], "status": "missing"}
        self.assertEqual(self.assess(q.seal(row))["verdict"], "UNKNOWN")

    def test_ttl_boundary_and_no_freshness_laundering(self):
        start = q.timestamp(self.row["started_at"])
        self.assertEqual(self.assess(now=start + dt.timedelta(hours=24))["verdict"], "PASS")
        self.assertEqual(self.assess(now=start + dt.timedelta(hours=24, microseconds=1))["verdict"], "UNKNOWN")

    def test_duplicate_cases_and_screen_cannot_confirm(self):
        row = copy.deepcopy(self.row)
        row["cases"][1] = row["cases"][0]
        self.assertEqual(self.assess(q.seal(row))["verdict"], "UNKNOWN")
        self.binding["stage"] = "screen"
        self.assertEqual(self.assess()["verdict"], "UNKNOWN")

    def test_invented_cases_and_permissive_infrastructure_failure_cannot_pass(self):
        row = copy.deepcopy(self.row)
        row["cases"] = ["invented-one", "invented-two"]
        for slot, name in zip(row["observations"], row["cases"]):
            slot["case_id"] = name
        row["observation_key"] = q.observation_key(row["binding"], row["cases"])
        self.assertEqual(self.assess(q.seal(row))["verdict"], "UNKNOWN")
        row = copy.deepcopy(self.row)
        row["observations"][-1].update(status="error", trace_complete=False)
        self.spec.update(max_failures=2, min_pass_rate=0)
        self.assertEqual(self.assess(q.seal(row))["verdict"], "UNKNOWN")

    def test_threshold_only_rejudge_does_not_change_key(self):
        key = self.row["observation_key"]
        self.spec["min_cases"] = 3
        self.assertEqual(self.assess()["verdict"], "UNKNOWN")
        self.assertEqual(key, q.observation_key(self.binding, self.row["cases"]))

    def test_invalid_numbers_and_policy_draft(self):
        self.assertFalse(q.validate_policy({"status": "draft"}))
        for value in (True, float("inf"), float("nan"), -1):
            with self.assertRaises(q.QualityError):
                q.number(value)
        self.policy["version"] = 1
        with self.assertRaises(core.SyncError):
            core.validate_policy(self.policy)

    def test_changed_transport_and_no_evidence_cannot_promote(self):
        with self.assertRaises(core.SyncError):
            core.build_profile(self.models, self.policy, core.normalize_tasks(self.data, "request_share"), self.catalog, bootstrap=True)
        self.models["providers"]["local"]["baseUrl"] = "https://changed.invalid"
        with self.assertRaises(q.QualityError):
            q.expected_binding(self.models, self.policy["quality"], "default", "local/alpha:high", "local/alpha", "vendor/alpha", "high")

    def test_incumbent_tie_preserved_and_whole_chain_required(self):
        rows = evidence_rows(self.models, self.policy)
        profile = {"model_mapping": {r: ["local/alpha:high"] for r in q.ROLES}}
        self.models["profiles"]["or-auto"] = profile
        result, _ = core.build_profile(self.models, self.policy, core.normalize_tasks(self.data, "request_share"), self.catalog, rows, allow_test=True)
        self.assertEqual(result["model_mapping"]["default"][0], "local/alpha:high")
        rows = [e for e in rows if e["binding"]["role"] != "critic"]
        with self.assertRaises(core.SyncError):
            core.build_profile(self.models, self.policy, core.normalize_tasks(self.data, "request_share"), self.catalog, rows, allow_test=True)

    def test_incumbent_effort_survives_earlier_variant_in_allowlist(self):
        self.policy["allowed_selectors"] = ["local/alpha:high", "local/alpha:xhigh"]
        self.models["profiles"]["or-auto"] = {"model_mapping": {r: ["local/alpha:xhigh"] for r in q.ROLES}}
        rows = evidence_rows(self.models, self.policy)
        profile, _ = core.build_profile(self.models, self.policy, core.normalize_tasks(self.data, "request_share"), self.catalog, rows, allow_test=True)
        self.assertTrue(all(chain == ["local/alpha:xhigh"] for chain in profile["model_mapping"].values()))
