"""Bounded process execution. Live mode requires bubblewrap; tests inject isolation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import shutil
import signal
import subprocess
import tempfile
import time

from .quality import QualityError, number
from .core import decode_yaml


def file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise QualityError("Runtime must be a regular pinned executable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def limits_check(limits: dict) -> None:
    for key in ("timeout_seconds", "memory_bytes", "cpu_seconds", "processes", "file_bytes", "output_bytes"):
        number(limits.get(key), integer=key != "timeout_seconds", minimum=0.001 if key == "timeout_seconds" else 1)


def execute(argv: list[str], cwd: Path, env: dict, limits: dict) -> dict:
    """Drain both streams under one byte cap; kill the entire process group."""
    limits_check(limits)

    def set_limits():
        for kind, key in ((resource.RLIMIT_AS, "memory_bytes"), (resource.RLIMIT_CPU, "cpu_seconds"),
                          (resource.RLIMIT_NPROC, "processes"), (resource.RLIMIT_FSIZE, "file_bytes")):
            resource.setrlimit(kind, (limits[key], limits[key]))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    started = time.monotonic()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    status = "exited"
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, preexec_fn=set_limits)
    poller = selectors.DefaultSelector()
    try:
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            poller.register(stream, selectors.EVENT_READ, name)
        while poller.get_map():
            if time.monotonic() - started >= limits["timeout_seconds"]:
                status = "timeout"
                break
            for key, _ in poller.select(min(0.05, max(0, limits["timeout_seconds"] - (time.monotonic() - started)))):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    poller.unregister(key.fileobj)
                    continue
                output[key.data].extend(chunk)
                if sum(map(len, output.values())) > limits["output_bytes"]:
                    status = "oversize"
                    break
            if status != "exited":
                break
        if status == "exited":
            try:
                process.wait(timeout=max(0, limits["timeout_seconds"] - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                status = "timeout"
    finally:
        # Also reap background children after an apparently successful parent.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=min(0.2, max(0, limits["timeout_seconds"] - (time.monotonic() - started))))
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        poller.close()
        process.stdout.close()
        process.stderr.close()
    return {"status": status, "exit_code": process.returncode,
            "elapsed": time.monotonic() - started,
            **{k: bytes(v[:limits["output_bytes"]]) for k, v in output.items()}}


class Sandbox:
    """Only fixed runtime paths are exposed. No host home, repository or SSH tree."""
    def __init__(self):
        self.bwrap = shutil.which("bwrap")
        if not self.bwrap:
            raise QualityError("Live evaluation requires bubblewrap")

    def command(self, argv: list[str], work: Path, *, network: bool = False,
                mounts: list[tuple[Path, str]] = ()) -> list[str]:
        command = [self.bwrap, "--die-with-parent", "--new-session", "--unshare-all"]
        if network:
            command.append("--share-net")
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists():
                command += ["--ro-bind", path, path]
        command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                    "--dir", "/home", "--dir", "/home/eval", "--dir", "/etc"]
        for path in ("/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts"):
            if Path(path).exists():
                command += ["--ro-bind", path, path]
        for source, target in mounts:
            command += ["--ro-bind", str(source), target]
        command += ["--bind", str(work), "/work", "--chdir", "/work", "--", *argv]
        return command

    def run(self, argv: list[str], work: Path, limits: dict, *, network=False, mounts=(), env=None) -> dict:
        environment = {"PATH": "/usr/bin:/bin", "HOME": "/home/eval", "LANG": "C.UTF-8",
                       "XDG_CONFIG_HOME": "/home/eval/config", "XDG_CACHE_HOME": "/tmp/cache",
                       "XDG_DATA_HOME": "/home/eval/data", "XDG_STATE_HOME": "/tmp/state"}
        environment.update(env or {})
        return execute(self.command(argv, work, network=network, mounts=mounts), work, environment, limits)


def parse_events(result: dict, provider: str, model: str) -> dict:
    identity = {"provider": provider, "model": model, "effective_effort": None,
                "backend_revision": None, "effort_attestation": "requested_only"}
    observed = {"status": "error", "identity": identity, "trace_complete": False,
                "response": "", "usage_cost": None, "elapsed": result["elapsed"],
                "raw_trace": result["stdout"].hex(), "raw_stderr": result.get("stderr", b"").hex(),
                "trace_hash": hashlib.sha256(result["stdout"]).hexdigest()}
    if result["status"] == "timeout":
        return {**observed, "status": "timeout"}
    if result["status"] != "exited" or result["exit_code"] != 0:
        return observed
    try:
        events = [json.loads(line) for line in result["stdout"].decode("utf-8").splitlines() if line.strip()]
        if not events or any(not isinstance(e, dict) for e in events):
            return observed
        messages, ended, started, open_turn, open_message = [], False, False, False, False
        allowed = {"session", "agent_start", "turn_start", "turn_end", "message_start", "message_update", "message_end", "agent_end"}
        for event in events:
            kind = event.get("type")
            if kind not in allowed or ended:
                return observed
            if kind == "session":
                if started:
                    return observed
                continue
            if kind == "agent_start":
                if started:
                    return observed
                started = True
                continue
            if not started:
                return observed
            if kind == "turn_start":
                if open_turn:
                    return observed
                open_turn = True
                continue
            if kind == "agent_end":
                terminal = event.get("messages")
                if (open_turn or open_message or not isinstance(terminal, list)
                        or event.get("stopReason", "completed") != "completed"):
                    return observed
                if any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant") for m in terminal):
                    return observed
                assistants = [m for m in terminal if isinstance(m, dict) and m.get("role") == "assistant"]
                if any(not isinstance(m.get(k), str) or not m[k] for m in assistants for k in ("provider", "model")):
                    return observed
                mismatch = next((m for m in assistants if m.get("provider") != provider or m.get("model") != model), None)
                if mismatch is not None:
                    return {**observed, "status": "substitution", "identity": {**identity,
                            "provider": mismatch.get("provider"), "model": mismatch.get("model")}}
                if assistants != messages:
                    return observed
                ended = True
                continue
            message = event.get("message", {})
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant") or not open_turn:
                return observed
            if kind == "message_start":
                if open_message:
                    return observed
                open_message = True
            elif kind in ("message_update", "message_end") and not open_message:
                return observed
            if kind == "message_end":
                open_message = False
            if kind == "turn_end":
                if open_message or event.get("toolResults") != [] or not messages or message != messages[-1]:
                    return observed
                open_turn = False
            if isinstance(message, dict) and message.get("role") == "assistant":
                if any(not isinstance(message.get(k), str) or not message[k] for k in ("provider", "model")):
                    return observed
                if message.get("provider") != provider or message.get("model") != model:
                    return {**observed, "status": "substitution", "identity": {**identity,
                            "provider": message.get("provider"), "model": message.get("model")}}
                if message.get("stopReason") in ("error", "aborted", "toolUse"):
                    return observed
                if kind == "message_end":
                    messages.append(message)
        if not ended or not messages:
            return observed
        text = "".join(part["text"] for part in messages[-1].get("content", []) if part.get("type") == "text")
        if not text.strip() or messages[-1].get("stopReason") not in ("stop", "length"):
            return observed
        costs = [m.get("usage", {}).get("cost", {}).get("total") for m in messages]
        cost = sum(number(c) for c in costs) if all(c is not None for c in costs) else None
        return {**observed, "status": "completed", "trace_complete": True, "response": text, "usage_cost": cost}
    except (ValueError, TypeError, KeyError, AttributeError):
        return observed


class GjcRunner:
    def __init__(self, runtime: Path, expected_hash: str, agent_dir: Path, limits: dict,
                 *, _test_sandbox=None):
        limits_check(limits)
        if file_hash(runtime) != expected_hash:
            raise QualityError("GJC runtime hash differs from the approved runtime")
        # A shell launcher hash does not pin the runtime it happens to launch.
        if _test_sandbox is None and runtime.read_bytes()[:2] == b"#!":
            raise QualityError("Pin a self-contained GJC executable, not an unbound launcher")
        if agent_dir.is_symlink() or not agent_dir.is_dir():
            raise QualityError("A dedicated single-connection agent directory is required")
        allowed = {"models.yml", "auth.json"}
        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in agent_dir.iterdir()):
            raise QualityError("Evaluation connection directory contains unexpected files")
        self.runtime, self.expected_hash, self.agent_dir, self.limits = runtime, expected_hash, agent_dir, limits
        self.sandbox = _test_sandbox or Sandbox()
        self.provenance = "test" if _test_sandbox else "live"

    def run(self, binding: dict, prompt: str, *, timeout: float | None = None) -> dict:
        if file_hash(self.runtime) != self.expected_hash:
            raise QualityError("GJC runtime changed before evaluation")
        if self.provenance == "live":
            source = self.agent_dir / "models.yml"
            model_config = decode_yaml(source.read_text())
            providers = model_config.get("providers", {})
            if set(providers) != {binding["provider"]} or model_config.get("profiles"):
                raise QualityError("Evaluation requires one provider without profile routing")
            rows = providers[binding["provider"]].get("models", [])
            if len(rows) != 1 or rows[0].get("id") != binding["model"]:
                raise QualityError("Evaluation connection must contain exactly the requested model")
            from .quality import config_hash
            if config_hash(model_config, binding["provider"], binding["model"]) != binding["config_hash"]:
                raise QualityError("Evaluation transport differs from approved configuration")
        argv = ["/runtime/gjc", "--print", "--mode", "json", "--provider", binding["provider"],
                "--model", binding["model"], "--thinking", binding["requested_effort"], "--no-tools",
                "--no-mcp", "--no-lsp", "--no-pty", "--no-session", "--no-rules", "--no-title",
                "--", "Evaluation task; return only the requested artifact.\n" + prompt]
        limits = dict(self.limits)
        if timeout is not None:
            limits["timeout_seconds"] = min(limits["timeout_seconds"], timeout)
        with tempfile.TemporaryDirectory(prefix="gjc-eval-") as directory:
            work = Path(directory)
            # Runtime caches must be writable, but never in the operator's source
            # directory. Only the allowlisted single-connection files are copied.
            connection = work / "connection"
            connection.mkdir(mode=0o700)
            for path in self.agent_dir.iterdir():
                if path.is_symlink() or path.name not in {"models.yml", "auth.json"}:
                    raise QualityError("Connection directory changed during evaluation")
                target = connection / path.name
                target.write_bytes(path.read_bytes())
                target.chmod(0o600)
            result = self.sandbox.run(argv, work, limits, network=True,
                        mounts=[(self.runtime, "/runtime/gjc")],
                        env={"GJC_CODING_AGENT_DIR": "/work/connection", "PI_CODING_AGENT_DIR": "/work/connection"})
        if file_hash(self.runtime) != self.expected_hash:
            raise QualityError("GJC runtime changed during evaluation")
        return parse_events(result, binding["provider"], binding["model"])
