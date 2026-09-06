import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

from gjc_preset_sync import core as c
from gjc_preset_sync.__main__ import main


def fixture():
    models = {"providers": {"local": {"models": [
        {"id": "alpha", "contextWindow": 100000, "thinking": {"levels": ["high", "xhigh"]}},
        {"id": "beta", "contextWindow": 100000, "thinking": {"levels": ["high"]}},
    ]}}, "profiles": {"mine": {"required_providers": ["local"], "model_mapping": {
        "default": ["local/alpha:high", "local/beta:high"]}}}}
    policy = c.initial_policy(models)
    catalog = {"data": [{"id": f"vendor/{name}", "context_length": 100000,
                         "supported_parameters": ["tools"], "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
                        for name in ("alpha", "beta")]}
    data = {"data": {"as_of": dt.datetime.now(dt.timezone.utc).date().isoformat(), "window_days": 7,
                     "classifications": [{"tag": tag, "models": [
                         {"id": "vendor/alpha", "tag_usage_share": .2, "tag_token_share": .7},
                         {"id": "vendor/beta", "tag_usage_share": .8, "tag_token_share": .3},
                     ]} for tag in ("code:general_impl", "code:debugging", "agent:web_search")]}}
    return models, policy, catalog, data


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.models, self.policy, self.catalog, self.data = fixture()

    def build(self):
        return c.build_profile(self.models, self.policy, c.normalize_tasks(self.data, "request_share"), self.catalog)

    def test_rank_and_preserve_exact_transport_selector(self):
        profile, report = self.build()
        self.assertEqual(profile["model_mapping"]["executor"][0], "local/beta:high")
        self.assertEqual(report["metric"], "request_share")
        self.assertEqual(report["inference_calls"], 0)

    def test_token_metric_is_distinct(self):
        profile, _ = c.build_profile(self.models, self.policy, c.normalize_tasks(self.data, "token_share"), self.catalog)
        self.assertEqual(profile["model_mapping"]["default"][0], "local/alpha:high")

    def test_never_label_usage_as_spend(self):
        with self.assertRaises(c.SyncError):
            c.normalize_tasks(self.data, "spend_share")

    def test_stale_snapshot_fails(self):
        self.data["data"]["as_of"] = "2000-01-01"
        with self.assertRaises(c.SyncError): self.build()

    def test_future_snapshot_fails(self):
        self.data["data"]["as_of"] = "2999-01-01"
        with self.assertRaises(c.SyncError): self.build()

    def test_date_only_previous_utc_day_is_valid(self):
        c.age_check("2026-09-04", 72, dt.datetime(2026, 9, 5, 23, tzinfo=dt.timezone.utc))

    def test_duplicate_task_fails(self):
        self.data["data"]["classifications"].append(self.data["data"]["classifications"][0])
        with self.assertRaises(c.SyncError): self.build()

    def test_duplicate_ranked_model_fails(self):
        entries = self.data["data"]["classifications"][0]["models"]
        entries.append(entries[0])
        with self.assertRaises(c.SyncError): self.build()

    def test_nonfinite_share_fails(self):
        self.data["data"]["classifications"][0]["models"][0]["tag_usage_share"] = float("nan")
        with self.assertRaises(c.SyncError): self.build()

    def test_fraction_overflow_fails(self):
        self.data["data"]["classifications"][0]["models"][0]["tag_usage_share"] = .9
        with self.assertRaises(c.SyncError): self.build()

    def test_catalog_ambiguity_is_not_fuzzy_matched(self):
        item = copy.deepcopy(self.catalog["data"][1]); item["id"] = "other/beta"
        self.catalog["data"].append(item)
        profile, report = self.build()
        self.assertEqual(profile["model_mapping"]["default"], ["local/alpha:high"])
        self.assertEqual(report["rejected"][0]["reason"], "unmapped_or_ambiguous")

    def test_explicit_alias_resolves_ambiguity(self):
        item = copy.deepcopy(self.catalog["data"][1]); item["id"] = "other/beta"
        self.catalog["data"].append(item)
        self.policy["aliases"] = {"local/beta": "vendor/beta"}
        self.assertEqual(self.build()[0]["model_mapping"]["default"][0], "local/beta:high")

    def test_tool_requirement_filters(self):
        self.catalog["data"][1]["supported_parameters"] = []
        self.assertEqual(self.build()[0]["model_mapping"]["default"], ["local/alpha:high"])

    def test_local_tool_incompatibility_filters(self):
        self.models["providers"]["local"]["models"][1]["compat"] = {"supportsToolChoice": False}
        self.assertEqual(self.build()[0]["model_mapping"]["default"], ["local/alpha:high"])

    def test_context_checks_local_cap_not_only_remote(self):
        self.models["providers"]["local"]["models"][1]["contextWindow"] = 100
        self.assertEqual(self.build()[0]["model_mapping"]["default"], ["local/alpha:high"])

    def test_price_units_are_per_million(self):
        self.policy["filters"]["max_prompt_per_million"] = .5
        with self.assertRaises(c.SyncError): self.build()
        self.policy["filters"]["max_prompt_per_million"] = 1
        self.build()

    def test_unknown_price_rejected_when_cap_enabled(self):
        self.policy["filters"]["max_prompt_per_million"] = 1
        self.catalog["data"][1]["pricing"] = {}
        self.assertEqual(self.build()[0]["model_mapping"]["default"], ["local/alpha:high"])

    def test_missing_role_tag_never_partial_update(self):
        self.policy["roles"]["critic"]["tasks"] = {"missing:task": 1}
        with self.assertRaises(c.SyncError): self.build()

    def test_whitelist_is_enforced(self):
        self.policy["allowed_selectors"] = ["local/alpha:high"]
        self.assertEqual(self.build()[0]["model_mapping"]["default"], ["local/alpha:high"])

    def test_unknown_effort_capability_not_guessed(self):
        self.policy["roles"]["default"]["effort"] = "xhigh"
        self.assertEqual(self.build()[0]["model_mapping"]["default"], ["local/alpha:xhigh"])

    def test_multiple_efforts_of_one_transport_dont_fill_fallback_chain(self):
        self.policy["allowed_selectors"].append("local/alpha:xhigh")
        self.assertEqual(len(self.build()[0]["model_mapping"]["default"]), 2)

    def test_exact_colon_bearing_id_before_effort_suffix(self):
        self.models["providers"]["local"]["models"].append({"id": "alpha:high"})
        base, _, effort = c.local_model("local/alpha:high", self.models)
        self.assertEqual(base, "local/alpha:high"); self.assertIsNone(effort)

    def test_top_n_shares_need_not_sum_to_one(self):
        for task in self.data["data"]["classifications"]:
            task["models"][1]["tag_usage_share"] = .1
        self.build()

    def test_spend_import_explicit_and_distinct(self):
        payload = {"as_of": self.data["data"]["as_of"], "metric": "spend_share", "window_days": 7,
                   "source_url": c.SOURCE, "tasks": {"code:general_impl": {"vendor/alpha": .3}}}
        self.assertEqual(c.normalize_spend(payload)["metric"], "spend_share")
        payload["source_url"] = "https://example.invalid"
        with self.assertRaises(c.SyncError): c.normalize_spend(payload)


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "models.yml"
        self.state = self.root / "state"
        self.original = ('# credential section must remain byte-exact\nproviders:\n'
                         '  local:\n    apiKey: "NOT-A-REAL-KEY"\n    models: []\n'
                         'profiles:\n  mine:\n    required_providers: []\n    model_mapping:\n      default: local/alpha\n'
                         'equivalence:\n  marker: kept\n')
        self.path.write_text(self.original)
        self.profile = {"required_providers": [], "model_mapping": {"default": ["local/beta:high"]}}

    def apply(self):
        return c.mutate(self.path, self.state, "or-auto", self.profile)

    def test_preserve_unrelated_sections_byte_for_byte(self):
        self.apply()
        result = self.path.read_text()
        self.assertEqual(result.split("profiles:")[0], self.original.split("profiles:")[0])
        self.assertTrue(result.endswith("equivalence:\n  marker: kept\n"))
        self.assertEqual(c.decode_yaml(result)["profiles"]["mine"], c.decode_yaml(self.original)["profiles"]["mine"])

    def test_idempotent_no_extra_backup(self):
        self.assertEqual(self.apply(), "applied")
        self.assertEqual(self.apply(), "unchanged")
        self.assertEqual(len(list((self.state / "backups").glob("*.yml"))), 1)

    def test_rollback_scoped_preserves_later_unrelated_edits(self):
        self.apply()
        self.path.write_text(self.path.read_text().replace("marker: kept", "marker: later"))
        c.mutate(self.path, self.state, rollback=True)
        current = c.decode_yaml(self.path.read_text())
        self.assertNotIn("or-auto", current["profiles"])
        self.assertEqual(current["equivalence"]["marker"], "later")

    def test_manual_managed_edits_are_not_overwritten(self):
        self.apply()
        self.path.write_text(self.path.read_text().replace("local/beta:high", "local/other"))
        with self.assertRaises(c.SyncError): self.apply()

    def test_collision_is_not_claimed(self):
        with self.assertRaises(c.SyncError): c.mutate(self.path, self.state, "mine", self.profile)

    def test_existing_null_profile_name_is_not_claimed(self):
        self.path.write_text(self.original.replace("profiles:\n", "profiles:\n  or-auto: null\n"))
        with self.assertRaises(c.SyncError): self.apply()

    def test_lock_busy(self):
        lock = Path(str(self.path) + ".lock"); lock.mkdir()
        with self.assertRaises(c.SyncError): self.apply()
        self.assertTrue(lock.exists())

    def test_lock_released_after_failure(self):
        self.path.write_text("providers: []\nprofiles: broken\n")
        with self.assertRaises(c.SyncError): self.apply()
        self.assertFalse(Path(str(self.path) + ".lock").exists())

    def test_symlink_refused(self):
        alias = self.root / "alias.yml"; alias.symlink_to(self.path)
        with self.assertRaises(c.SyncError): c.mutate(alias, self.state, "or-auto", self.profile)

    def test_duplicate_keys_and_aliases_refused(self):
        for text in ("profiles: {}\nprofiles: {}\n", "a: &x {}\nb: *x\n"):
            with self.assertRaises(c.SyncError): c.patch_profiles(text, {"or-auto": self.profile})

    def test_invalid_yaml_error_does_not_echo_secret(self):
        try: c.decode_yaml("secret: [NOT-A-REAL-SECRET:")
        except c.SyncError as error: self.assertNotIn("NOT-A-REAL-SECRET", str(error))
        else: self.fail("Expected parse failure")

    def test_expected_hash_prevents_stale_plan(self):
        with self.assertRaises(c.SyncError):
            c.mutate(self.path, self.state, "or-auto", self.profile, expected_sha="wrong")
        self.assertEqual(self.path.read_text(), self.original)

    def test_state_cannot_be_reused_for_another_config(self):
        self.apply()
        second = self.root / "other.yml"; second.write_text(self.path.read_text())
        with self.assertRaises(c.SyncError): c.mutate(second, self.state, "or-auto", self.profile)

    def test_private_file_permissions(self):
        self.apply()
        for p in (self.path, self.state / "state.json", next((self.state / "backups").glob("*.yml"))):
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_pending_transaction_recovery_after_config_write(self):
        original_write = c.atomic_write
        def crash(path, content):
            if path == self.state / "state.json": raise OSError("simulated crash")
            original_write(path, content)
        with patch.object(c, "atomic_write", side_effect=crash):
            with self.assertRaises(OSError): self.apply()
        self.assertTrue((self.state / "pending.json").exists())
        self.assertEqual(self.apply(), "unchanged")
        self.assertFalse((self.state / "pending.json").exists())

    def test_pending_before_write_is_aborted_safely(self):
        original_write = c.atomic_write
        def crash(path, content):
            if path == self.path: raise OSError("simulated crash")
            original_write(path, content)
        with patch.object(c, "atomic_write", side_effect=crash):
            with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.apply(), "applied")

    def test_profiles_section_can_be_appended(self):
        self.path.write_text("providers: {}\n")
        self.apply()
        self.assertIn("or-auto", c.decode_yaml(self.path.read_text())["profiles"])


class NetworkAndCliTests(unittest.TestCase):
    def test_network_is_get_only_and_host_fixed(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"data": []}'
        with patch.object(c.urllib.request, "build_opener", return_value=opener):
            c.fetch_json("tasks", "TEST-KEY")
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.full_url, c.URLS["tasks"])
        self.assertIsNone(req.data)
        with self.assertRaises(c.SyncError): c.fetch_json("https://evil.invalid", "TEST-KEY")

    def test_models_fetch_does_not_send_authorization(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"data": []}'
        with patch.object(c.urllib.request, "build_opener", return_value=opener):
            c.fetch_json("models", "TEST-KEY")
        self.assertIsNone(opener.open.call_args.args[0].get_header("Authorization"))

    def test_redirects_refused(self):
        with self.assertRaises(c.SyncError): c.NoRedirect().redirect_request(None, None, 302, None, {}, "https://evil.invalid")

    def test_missing_key_makes_no_request(self):
        with patch.object(c.urllib.request, "build_opener") as opener:
            with self.assertRaises(c.SyncError): c.fetch_json("tasks", None)
            opener.assert_not_called()

    def test_http_errors_do_not_echo_body_or_key(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(c.URLS["tasks"], 401, "SECRET-MSG", {}, None)
        with patch.object(c.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(c.SyncError) as caught: c.fetch_json("tasks", "SECRET-KEY")
        self.assertNotIn("SECRET", str(caught.exception))

    def test_rate_limit_retry_is_bounded(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(c.URLS["tasks"], 429, "limited", {"Retry-After": "1"}, None)
        with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep"):
            with self.assertRaises(c.SyncError): c.fetch_json("tasks", "TEST-KEY")
        self.assertEqual(opener.open.call_count, 3)

    def test_cli_plan_does_not_touch_models_and_snapshot_needs_extra_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models, policy, catalog, data = fixture()
            c.atomic_write(root / "models.yml", c.yaml.safe_dump(models).encode())
            for name, obj in (("policy", policy), ("catalog", catalog), ("tasks", data)):
                c.atomic_write(root / (name + ".json"), c.json_bytes(obj))
            original = (root / "models.yml").read_bytes()
            args = ["--models", str(root / "models.yml"), "--policy", str(root / "policy.json"),
                    "--state-dir", str(root / "state"), "--tasks-file", str(root / "tasks.json"),
                    "--catalog-file", str(root / "catalog.json")]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["plan"] + args), 0)
                self.assertEqual(main(["sync", "--apply"] + args), 2)
                self.assertEqual((root / "models.yml").read_bytes(), original)
                self.assertEqual(main(["sync", "--apply", "--allow-snapshot-apply"] + args), 0)
                self.assertEqual(main(["rollback", "--apply", "--models", str(root / "models.yml"),
                                       "--state-dir", str(root / "state")]), 0)

    def test_cache_hit_avoids_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, policy, catalog, tasks = fixture()
            c.atomic_write(root / "data-cache.json", c.json_bytes({"fetched_at_epoch": c.time.time(), "tasks": tasks, "models": catalog}))
            with patch.object(c, "fetch_json") as fetch:
                self.assertTrue(c.get_dataset(policy, root)[2]); fetch.assert_not_called()

    def test_fetch_failure_does_not_replace_good_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, policy, catalog, tasks = fixture()
            c.atomic_write(root / "data-cache.json", c.json_bytes({"fetched_at_epoch": 0, "tasks": tasks, "models": catalog}))
            original = (root / "data-cache.json").read_bytes()
            with patch.object(c, "fetch_json", side_effect=c.SyncError("offline")):
                with self.assertRaises(c.SyncError): c.get_dataset(policy, root)
            self.assertEqual((root / "data-cache.json").read_bytes(), original)


if __name__ == "__main__": unittest.main()
