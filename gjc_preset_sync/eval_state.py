"""Persistent local evaluation quotas. Not a billing or hostile-user boundary."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import time

from .quality import QualityError, number, sha, validate_quota, seal


def secure_directory(path: Path) -> None:
    absolute = path.absolute()
    for parent in [*reversed(absolute.parents), absolute]:
        if parent.is_symlink():
            raise QualityError("Evaluation paths cannot contain symlinks")
    absolute.mkdir(parents=True, exist_ok=True, mode=0o700)
    if absolute.stat().st_uid != os.getuid():
        raise QualityError("Evaluation directory has a different owner")
    absolute.chmod(0o700)


def read_private(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8 * 1024 * 1024:
            raise QualityError("Unsafe private evaluation file")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise QualityError("Invalid evaluation object")
    return value


def save_private(path: Path, value: dict, *, exclusive: bool = False) -> None:
    secure_directory(path.parent)
    if path.is_symlink():
        raise QualityError("Unsafe evaluation target")
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EvalState:
    def __init__(self, root: Path, limits: dict, *, clock=time.time):
        validate_quota(limits)
        self.root, self.limits, self.clock = root, dict(limits), clock
        secure_directory(root)

    @contextlib.contextmanager
    def _transaction(self):
        fd = os.open(self.root / "quota.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            path = self.root / "quota.json"
            data = read_private(path) if path.exists() else {"version": 1, "last_time": 0,
                    "period_start": 0, "limits": self.limits, "history": {}, "runs": {}, "approvals": []}
            now = self.clock()
            number(now)
            if data["version"] != 1 or now < data["last_time"]:
                raise QualityError("Evaluation clock moved backwards or state is unsupported")
            data["last_time"] = now
            old = data["limits"]
            if now >= data["period_start"] + old["period_seconds"]:
                data["limits"] = self.limits
                data["period_start"] = math.floor(now / self.limits["period_seconds"]) * self.limits["period_seconds"]
            yield data, now
            save_private(path, data)
        finally:
            os.close(fd)

    def _usage(self, data: dict) -> dict:
        start = data["period_start"]
        launches, seconds, candidates = 0, 0.0, set()
        for run in data["runs"].values():
            charge = False
            for slot in run["slots"]:
                if slot["state"] in ("reserved", "started"):
                    launches += 1
                    seconds += slot["timeout"]
                    charge = True
                elif slot["state"] == "finished" and slot["ended_at"] >= start:
                    launches += 1
                    seconds += slot["elapsed"]
                    charge = True
            if charge:
                candidates.add(run["candidate"])
        return {"launches": launches, "seconds": seconds, "candidates": len(candidates), "candidate_keys": candidates}

    def preview(self) -> dict:
        with self._transaction() as (data, now):
            usage = self._usage(data)
            return {"period_start": data["period_start"], "limits": data["limits"],
                    "usage": {k: v for k, v in usage.items() if k != "candidate_keys"}}

    def reserve(self, run_id: str, approval_id: str, key: str, candidate: str,
                role: str, stage: str, cases: list[str], timeout: float,
                *, reuse_check=None, manifest: dict | None = None) -> bool:
        number(timeout, minimum=0.001)
        if not cases or len(set(cases)) != len(cases) or stage not in ("screen", "confirm"):
            raise QualityError("Invalid stage reservation")
        with self._transaction() as (data, now):
            if reuse_check is not None and reuse_check():
                return False
            if run_id in data["runs"] or approval_id in data["approvals"]:
                raise QualityError("Evaluation approval or run was already consumed")
            for run in data["runs"].values():
                if (run["key"] == key or (run["candidate"] == candidate and run["role"] == role)) and run.get("active", True):
                    raise QualityError("An evaluation is already reserved")
            history_key = sha([candidate, role])
            history = data["history"].get(history_key)
            if history and now - history["at"] < data["limits"]["reevaluation_cooldown_seconds"]:
                if not (history["stage"] == "screen" and stage == "confirm" and not history["confirmed"]):
                    raise QualityError("Evaluation cooldown is active")
            usage, limits = self._usage(data), data["limits"]
            if (usage["launches"] + len(cases) > limits["max_launches_per_period"]
                    or usage["seconds"] + timeout * len(cases) > limits["max_reserved_wall_seconds_per_period"]
                    or usage["candidates"] + (candidate not in usage["candidate_keys"]) > limits["max_new_candidates_per_period"]):
                raise QualityError("Evaluation period quota exhausted")
            data["runs"][run_id] = {"key": key, "candidate": candidate, "role": role, "stage": stage,
                    "history_key": history_key, "reserved_at": now, "active": True,
                    "slots": [{"case_id": c, "state": "reserved", "timeout": timeout} for c in cases]}
            data["approvals"].append(approval_id)
            if manifest is not None:
                save_private(self.root / "runs" / sha(run_id) / "manifest.json", manifest, exclusive=True)
            return True

    def start(self, run_id: str, case_id: str) -> None:
        with self._transaction() as (data, now):
            run = data["runs"][run_id]
            slot = next(s for s in run["slots"] if s["case_id"] == case_id)
            if slot["state"] != "reserved":
                raise QualityError("Evaluation slot is not available")
            usage, limits = self._usage(data), data["limits"]
            if (usage["launches"] > limits["max_launches_per_period"] or usage["seconds"] > limits["max_reserved_wall_seconds_per_period"]
                    or usage["candidates"] > limits["max_new_candidates_per_period"]):
                raise QualityError("Outstanding reservations exceed this period's limits")
            slot.update(state="started", started_at=now)
            previous = data["history"].get(run["history_key"], {})
            data["history"][run["history_key"]] = {"at": now, "stage": run["stage"],
                    "confirmed": run["stage"] == "confirm" or previous.get("confirmed", False)}

    def finish(self, run_id: str, case_id: str, elapsed: float) -> None:
        number(elapsed)
        with self._transaction() as (data, now):
            slot = next(s for s in data["runs"][run_id]["slots"] if s["case_id"] == case_id)
            if slot["state"] != "started":
                raise QualityError("Only a started slot can finish")
            # Wall-time overrun is charged, not silently clipped to a budget.
            slot.update(state="finished", elapsed=elapsed, ended_at=now)

    def cancel_unstarted(self, run_id: str) -> None:
        with self._transaction() as (data, now):
            for slot in data["runs"][run_id]["slots"]:
                if slot["state"] == "reserved":
                    slot["state"] = "cancelled"
            if all(slot["state"] == "cancelled" for slot in data["runs"][run_id]["slots"]):
                data["runs"][run_id]["active"] = False

    def record_observation(self, run_id: str, case_id: str, observation: dict, artifact: str) -> str:
        path = self.root / "runs" / sha(run_id) / (sha(case_id) + ".json")
        save_private(path, {"observation": observation, "artifact": artifact}, exclusive=True)
        return str(path.relative_to(self.root))

    def publish(self, run_id: str, evidence: dict) -> None:
        """Hold the reservation until both immutable evidence and its index exist."""
        with self._transaction() as (data, now):
            run = data["runs"][run_id]
            if not run["active"] or any(slot["state"] in ("started", "reserved") for slot in run["slots"]):
                raise QualityError("Run is not ready to publish observations")
            if evidence.get("observation_key") != run["key"]:
                raise QualityError("Published evidence differs from reservation")
            save_private(self.root / "runs" / (sha(run_id) + ".json"), evidence, exclusive=True)
            save_private(self.evidence_path(run["key"]), evidence)
            run["active"] = False

    def recover_evidence(self, run_id: str) -> dict:
        """Inspect durable partial observations without freeing budget or rerunning."""
        with self._transaction() as (data, now):
            run = data["runs"][run_id]
            record = read_private(self.root / "runs" / sha(run_id) / "manifest.json")
            manifest = record["manifest"]
            rows = []
            for slot in run["slots"]:
                path = self.root / "runs" / sha(run_id) / (sha(slot["case_id"]) + ".json")
                if slot["state"] == "finished" and path.exists():
                    rows.append(read_private(path)["observation"])
                else:
                    rows.append({"case_id": slot["case_id"], "status": "missing"})
            return seal({"version": 1, "binding": manifest["binding"], "cases": manifest["cases"],
                    "observation_key": run["key"], "observations": rows, "provenance": record["provenance"],
                    "started_at": dt.datetime.fromtimestamp(run["reserved_at"], dt.timezone.utc).isoformat(),
                    "ended_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                    "recovery_snapshot": True, "monetary_cap_guaranteed": False})

    def evidence_path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise QualityError("Invalid observation key")
        return self.root / "evidence" / (key + ".json")
