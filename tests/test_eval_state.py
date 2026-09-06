import tempfile
import multiprocessing
from pathlib import Path
import unittest

from gjc_preset_sync.eval_state import EvalState
from gjc_preset_sync.quality import QualityError, sha
from eval_helpers import QUOTA


def reserve_in_process(root, barrier, result, name):
    state = EvalState(Path(root), QUOTA)
    barrier.wait(timeout=10)
    try:
        state.reserve(name, name, sha("shared"), "model", "default", "screen", ["case"], 10)
        result.put("reserved")
    except QualityError:
        result.put("blocked")


class StateTests(unittest.TestCase):
    def test_two_processes_cannot_reserve_the_same_observation(self):
        barrier = multiprocessing.Barrier(2)
        result = multiprocessing.Queue()
        processes = [multiprocessing.Process(target=reserve_in_process, args=(str(self.root), barrier, result, name)) for name in ("a", "b")]
        for process in processes:
            process.start()
        try:
            self.assertEqual(sorted([result.get(timeout=10), result.get(timeout=10)]), ["blocked", "reserved"])
        finally:
            for process in processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join()
            result.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000
        self.limits = {**QUOTA, "period_seconds": 100, "max_launches_per_period": 2,
                       "max_new_candidates_per_period": 1, "max_reserved_wall_seconds_per_period": 20}
        self.root = Path(self.tmp.name) / "state"
        self.state = EvalState(self.root, self.limits, clock=lambda: self.now)

    def reserve(self, run="run", approval="approval", key="key", candidate="model", stage="screen", cases=("a",)):
        self.state.reserve(run, approval, sha(key), candidate, "default", stage, list(cases), 10)

    def test_reservation_replay_concurrent_instance_and_quota(self):
        self.reserve()
        other = EvalState(self.root, self.limits, clock=lambda: self.now)
        with self.assertRaises(QualityError):
            other.reserve("other", "new", "key", "model", "default", "screen", ["a"], 10)
        self.state.start("run", "a")
        self.state.finish("run", "a", 1)
        self.state.publish("run", {"observation_key": sha("key")})
        with self.assertRaises(QualityError):
            self.reserve("new", "approval", "different")
        with self.assertRaises(QualityError):
            self.reserve("new", "new", "different", "othermodel")

    def test_first_confirm_allowed_but_repeated_confirm_blocked(self):
        self.reserve()
        self.state.start("run", "a")
        self.state.finish("run", "a", 1)
        self.state.publish("run", {"observation_key": sha("key")})
        self.reserve("confirm", "confirm", "confirm", stage="confirm")
        self.state.start("confirm", "a")
        self.state.finish("confirm", "a", 1)
        with self.assertRaises(QualityError):
            self.reserve("repeat", "repeat", "new", stage="confirm")

    def test_crash_started_consumed_restart_and_cancel_only_unstarted(self):
        self.reserve(cases=("a", "b"))
        self.state.start("run", "a")
        self.state = EvalState(self.root, self.limits, clock=lambda: self.now)
        self.state.cancel_unstarted("run")
        self.assertEqual(self.state.preview()["usage"]["launches"], 1)
        self.now = 1101
        self.assertEqual(self.state.preview()["usage"]["launches"], 1)

    def test_cancel_before_launch_refunds_candidate(self):
        self.reserve()
        self.state.cancel_unstarted("run")
        self.reserve("different", "different", "other", "differentmodel")
        self.assertEqual(self.state.preview()["usage"]["candidates"], 1)

    def test_clock_reverse_and_changed_period_cannot_reset_consumption(self):
        self.reserve()
        self.state.start("run", "a")
        self.state.finish("run", "a", 2)
        new = EvalState(self.root, {**self.limits, "period_seconds": 1}, clock=lambda: self.now)
        self.assertEqual(new.preview()["limits"]["period_seconds"], 100)
        self.now -= 1
        with self.assertRaises(QualityError):
            new.preview()

    def test_finished_cross_period_charge_does_not_disappear(self):
        self.now = 1099
        self.reserve()
        self.state.start("run", "a")
        self.now = 1115
        self.state.finish("run", "a", 16)
        usage = self.state.preview()["usage"]
        self.assertEqual((usage["launches"], usage["seconds"]), (1, 16))

    def test_completed_slots_still_hold_reservation_until_evidence_publication(self):
        self.reserve()
        self.state.start("run", "a")
        self.now += 40
        self.state.finish("run", "a", 1)
        with self.assertRaises(QualityError):
            self.reserve("other", "other", "key")
        self.state.publish("run", {"observation_key": sha("key")})
        before = self.state.preview()["usage"]
        self.assertFalse(self.state.reserve("other", "other", "key", "model", "default", "screen", ["a"], 10,
                                           reuse_check=lambda: True))
        self.assertEqual(self.state.preview()["usage"], before)
