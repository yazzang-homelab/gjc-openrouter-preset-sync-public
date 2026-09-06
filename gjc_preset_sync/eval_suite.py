"""Small synthetic coverage pack, not a claim of general role competence."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tempfile

from .gjc_runner import Sandbox
from .quality import QualityError, sha

VERSION = "bounded-artifacts-1"
COVERAGE = {"executor": "patch-correctness", "critic": "defect-localization",
            "default": "bounded-problem-solving", "planner": "executable-plan-feasibility",
            "architect": "bounded-design-realization"}


def _case(identifier, role, kind, problem, **fields):
    return {"id": identifier, "role": role, "stage": "confirm", "kind": kind, "problem": problem, **fields}


CASES = [
    _case("executor-parser", "executor", "patch", "Fix parse_csv for an empty input and preserve trimmed nonempty fields.",
          files={"solution.py": "def parse_csv(text):\n    return text.strip().split(',')\n"},
          oracle="from solution import parse_csv\nassert parse_csv('') == []\nassert parse_csv('  ') == []\nassert parse_csv(' a, b ') == ['a','b']\nassert parse_csv('a,,c') == ['a','','c']\n"),
    _case("executor-transaction", "executor", "patch", "Transfer a nonnegative amount exactly once per tx. Insufficient funds or negative input must raise ValueError without recording tx or changing balance.",
          files={"solution.py": "def transfer(state, tx, amount):\n    state['balance'] -= amount\n    state['seen'].add(tx)\n    return state['balance']\n"},
          oracle="from solution import transfer\ns={'balance':10,'seen':set()}\nassert transfer(s,'a',3)==7\nassert transfer(s,'a',3)==7\nfor x in (8,-1):\n try: transfer(s,'b',x)\n except ValueError: pass\n else: raise AssertionError('expected failure')\n assert s=={'balance':7,'seen':{'a'}}\nassert transfer(s,'c',0)==7\n"),
    _case("architect-isolation", "architect", "patch", "isolate(functions) must call every function once, returning its value or None on Exception. A failed function must not prevent later functions from running.",
          files={"solution.py": "def isolate(functions):\n    return [f() for f in functions]\n"},
          oracle="from solution import isolate\nseen=[]\ndef bad():\n seen.append('bad')\n raise ValueError()\ndef good():\n seen.append('good')\n return 7\nassert isolate([bad,good])==[None,7]\nassert seen==['bad','good']\nassert isolate([])==[]\n"),
    _case("architect-coupling", "architect", "patch", "Remove domain's dependency on infrastructure. domain.price(qty, unit) must remain pure multiplication; infrastructure.quote(qty) must still use unit=4. Change only the supplied files.",
          files={"domain.py": "from infrastructure import UNIT\ndef price(qty, unit):\n    return qty * UNIT\n", "infrastructure.py": "UNIT = 4\ndef quote(qty):\n    from domain import price\n    return price(qty, UNIT)\n"},
          oracle="import ast\nfrom pathlib import Path\nfrom domain import price\nfrom infrastructure import quote\nassert price(3,7)==21\nassert price(0,8)==0\nassert quote(3)==12\ntree=ast.parse(Path('domain.py').read_text())\nassert not any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(tree))\n"),
    _case("default-transform", "default", "json", "Return JSON mapping each group to sum of nonnegative values. Omit negative values, retain groups whose sum is zero.",
          data=[["a", 3], ["b", -2], ["a", 4], ["c", 0], ["b", 5]], expected={"a": 7, "b": 5, "c": 0}),
    _case("default-intervals", "default", "json", "Return sorted merged closed intervals as JSON; touching endpoints merge.",
          data=[[5, 8], [1, 3], [3, 4], [10, 10], [7, 9]], expected=[[1, 4], [5, 9], [10, 10]]),
    _case("critic-boundary", "critic", "critic", "Report JSON {defect, line, input, expected}. Supply a concrete input demonstrating the defect, or defect=false for correct code. Contract: clamp(x, limit) accepts 0 <= x <= limit and returns x; otherwise raises ValueError. input is [x, limit]; expected is x or 'ValueError'.",
          code="def clamp(x, limit):\n    if x < 0 or x >= limit:\n        raise ValueError()\n    return x\n", bug="boundary", line=2),
    _case("critic-order", "critic", "critic", "Report JSON {defect, line, input, expected}. Contract: unique(xs) returns distinct items in first-occurrence order. input is an integer array.",
          code="def unique(xs):\n    return sorted(set(xs))\n", bug="order", line=2),
    _case("critic-clean", "critic", "critic", "Report JSON {defect:false} if correct, else {defect:true,line,input,expected}. Contract: total(xs) returns the sum of its integer elements, including negatives and empty lists.",
          code="def total(xs):\n    result = 0\n    for x in xs:\n        result += x\n    return result\n", bug="clean", line=0),
    _case("planner-recovery", "planner", "plan", "Return JSON array of operations to restore both services after storage failure without serving before storage is healthy. Operations: stop_a, stop_b, repair_storage, start_a, start_b. Repair requires both stopped. Start requires healthy storage. Each operation costs 1; at most 5 operations. Initial: both serving, storage failed. Goal: both serving, storage healthy.", scenario="recovery"),
    _case("planner-migration", "planner", "plan", "Return JSON array of operations: snapshot, disable_writes, copy, verify, switch, enable_writes. Copy requires snapshot and disabled writes; verify requires copy; switch requires verification and disabled writes. Enable requires switch. Snapshot requires writes still enabled. At most 6 operations. Goal: verified copy active, writes enabled.", scenario="migration"),
]

SCREENS = [
    _case("executor-screen", "executor", "patch", "Fix inc(n) to return n+1 for any integer.",
          files={"solution.py": "def inc(n):\n    return n\n"}, oracle="from solution import inc\nassert inc(-2)==-1\nassert inc(0)==1\nassert inc(10)==11\n"),
    _case("architect-screen", "architect", "patch", "ensure(fetch, fallback) should call fetch once and return fallback only when fetch raises Exception, not when fetch returns a false value.",
          files={"solution.py": "def ensure(fetch, fallback):\n    return fetch() or fallback\n"},
          oracle="from solution import ensure\nassert ensure(lambda:0,9)==0\ndef bad(): raise ValueError()\nassert ensure(bad,9)==9\n"),
    _case("default-screen", "default", "json", "Return JSON [minimum, maximum] of these integers.", data=[4, -3, 9, 0], expected=[-3, 9]),
    _case("critic-screen", "critic", "critic", "Return JSON {defect,line,input,expected}. Contract: first(xs) returns first item, or None when empty. Input is integer array.",
          code="def first(xs):\n    return xs[0]\n", bug="empty", line=2),
    _case("planner-screen", "planner", "plan", "Return JSON array: unlock before open. Each exactly once, at most two operations. Initial locked+closed, goal unlocked+open.", scenario="door"),
]
for _row in SCREENS:
    _row["stage"] = "screen"
CATALOG = {row["id"]: row for row in CASES + SCREENS}


def cases(role: str, stage: str) -> list[str]:
    return [key for key, value in CATALOG.items() if value["role"] == role and value["stage"] == stage]


def suite_hash() -> str:
    return sha({"version": VERSION, "cases": CATALOG})


def scorer_hash() -> str:
    return sha(Path(__file__).read_text())


def prompt(case_id: str) -> str:
    case = CATALOG[case_id]
    public = {k: v for k, v in case.items() if k in ("problem", "files", "code", "data")}
    if case["kind"] == "patch":
        public["format"] = "Return only a unified diff with --- a/path and +++ b/path headers; no Markdown fences."
    return json.dumps(public, ensure_ascii=False)


def apply_patch(files: dict[str, str], patch: str) -> dict[str, str]:
    """Strict unified diff for existing allowed text files. No additions/deletions."""
    lines, result, index, changed = patch.splitlines(keepends=True), dict(files), 0, set()
    while index < len(lines):
        if lines[index].startswith("diff --git ") or lines[index].startswith("index "):
            index += 1
            continue
        if not lines[index].startswith("--- a/") or index + 1 >= len(lines):
            raise QualityError("Invalid artifact patch header")
        path = lines[index][6:].strip()
        if path not in files or path in changed or lines[index + 1].strip() != "+++ b/" + path:
            raise QualityError("Artifact patch escapes its allowed files")
        changed.add(path)
        source, output, cursor = files[path].splitlines(keepends=True), [], 0
        index += 2
        hunks = 0
        while index < len(lines) and lines[index].startswith("@@"):
            match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?", lines[index])
            if not match:
                raise QualityError("Invalid artifact patch hunk")
            old_start, old_count, new_start, new_count = (int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1))
            pos = max(0, old_start - 1)
            if pos < cursor or pos > len(source):
                raise QualityError("Overlapping artifact patch")
            output.extend(source[cursor:pos])
            if max(0, new_start - 1) != len(output):
                raise QualityError("Invalid artifact patch target offset")
            cursor, used, added = pos, 0, 0
            index += 1
            while index < len(lines) and lines[index][:1] in (" ", "+", "-") and not lines[index].startswith("--- a/"):
                line = lines[index]
                if line[0] in " -":
                    if cursor >= len(source) or source[cursor] != line[1:]:
                        raise QualityError("Artifact patch context differs")
                    cursor, used = cursor + 1, used + 1
                if line[0] in " +":
                    output.append(line[1:])
                    added += 1
                index += 1
            if (used, added) != (old_count, new_count):
                raise QualityError("Artifact patch counts differ")
            hunks += 1
        if not hunks:
            raise QualityError("Artifact patch contains no hunks")
        result[path] = "".join(output + source[cursor:])
    if not changed:
        raise QualityError("Empty artifact patch")
    return result


def _plan(scenario: str, steps: list) -> bool:
    if not isinstance(steps, list) or any(not isinstance(x, str) for x in steps) or len(set(steps)) != len(steps):
        return False
    if scenario == "door":
        return steps == ["unlock", "open"]
    if scenario == "recovery":
        running, healthy = {"a", "b"}, False
        if len(steps) > 5:
            return False
        for step in steps:
            if step in ("stop_a", "stop_b") and step[-1] in running:
                running.remove(step[-1])
            elif step == "repair_storage" and not running:
                healthy = True
            elif step in ("start_a", "start_b") and healthy and step[-1] not in running:
                running.add(step[-1])
            else:
                return False
        return healthy and running == {"a", "b"}
    state = {"writes": True, "snapshot": False, "copied": False, "verified": False, "active": False}
    if len(steps) > 6:
        return False
    for step in steps:
        if step == "snapshot" and state["writes"]:
            state["snapshot"] = True
        elif step == "disable_writes" and state["writes"]:
            state["writes"] = False
        elif step == "copy" and state["snapshot"] and not state["writes"]:
            state["copied"] = True
        elif step == "verify" and state["copied"]:
            state["verified"] = True
        elif step == "switch" and state["verified"] and not state["writes"]:
            state["active"] = True
        elif step == "enable_writes" and state["active"]:
            state["writes"] = True
        else:
            return False
    return all(state.values())


def _critic(case: dict, value: dict) -> bool:
    if not isinstance(value, dict) or type(value.get("defect")) is not bool:
        return False
    if case["bug"] == "clean":
        return value == {"defect": False}
    if value.get("defect") is not True or type(value.get("line")) is not int or value["line"] != case["line"]:
        return False
    data = value.get("input")
    if not isinstance(data, list) or len(data) > 100 or any(type(x) is not int for x in data):
        return False
    if case["bug"] == "boundary":
        if len(data) != 2:
            return False
        x, limit = data
        buggy = "ValueError" if x < 0 or x >= limit else x
        reference = "ValueError" if x < 0 or x > limit else x
    elif case["bug"] == "order":
        buggy, reference = sorted(set(data)), list(dict.fromkeys(data))
    else:
        buggy, reference = (data[0] if data else "IndexError"), (data[0] if data else None)
    return buggy != reference and sha(value.get("expected")) == sha(reference)


class GradingUnavailable(QualityError):
    """The oracle did not produce an attributable terminal verdict."""


def grade(case_id: str, response: str, *, sandbox=None, limits=None) -> bool:
    case = CATALOG[case_id]
    if not isinstance(response, str) or len(response) > 128 * 1024:
        return False
    try:
        if case["kind"] == "patch":
            files = apply_patch(case["files"], response)
            forbidden = {"eval", "exec", "compile", "open", "input", "globals", "locals", "vars",
                         "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help",
                         "SystemExit", "BaseException", "MemoryError", "object", "type", "super"}
            for text in files.values():
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import) or (isinstance(node, ast.ImportFrom) and node.module != "domain"):
                        return False
                    if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in forbidden):
                        return False
                    if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                        return False
                    if isinstance(node, (ast.ClassDef, ast.Global, ast.Nonlocal)):
                        return False
            if limits is None:
                raise GradingUnavailable("Grading resource limits are required")
            try:
                boundary = sandbox or Sandbox()
            except QualityError:
                raise GradingUnavailable("Grading isolation is unavailable") from None
            with tempfile.TemporaryDirectory(prefix="grade-") as directory:
                work = Path(directory)
                for name, text in files.items():
                    (work / name).write_text(text)
                # Oracle lives outside writable/model-supplied artifact files.
                with tempfile.TemporaryDirectory(prefix="oracle-") as hidden:
                    oracle = Path(hidden) / "check.py"
                    try:
                        compile(case["oracle"], "oracle", "exec")
                    except SyntaxError:
                        raise GradingUnavailable("Invalid trusted oracle") from None
                    oracle.write_text("try:\n" + "".join("    " + line + "\n" for line in case["oracle"].splitlines()) +
                        "except (OSError, MemoryError, SystemError):\n    print('GJC_ORACLE_UNAVAILABLE')\n"
                        "except BaseException:\n    print('GJC_ORACLE_FAIL')\n"
                        "else:\n    print('GJC_ORACLE_PASS')\n")
                    try:
                        result = boundary.run(["/usr/bin/python3", "-B", "/oracle/check.py"], work, limits,
                                network=False, mounts=[(Path(hidden), "/oracle")], env={"PYTHONPATH": "/work"})
                    except QualityError:
                        raise GradingUnavailable("Grading execution is unavailable") from None
                if (result["status"] != "exited" or result["exit_code"] != 0
                        or result["stdout"] not in (b"GJC_ORACLE_PASS\n", b"GJC_ORACLE_FAIL\n")):
                    raise GradingUnavailable("Grader did not return a valid terminal verdict")
                return result["stdout"] == b"GJC_ORACLE_PASS\n"
        value = json.loads(response)
        if case["kind"] == "plan":
            return _plan(case["scenario"], value)
        if case["kind"] == "critic":
            return _critic(case, value)
        return sha(value) == sha(case["expected"])
    except GradingUnavailable:
        raise
    except OSError:
        raise GradingUnavailable("Grading storage or process is unavailable") from None
    except (ValueError, KeyError, TypeError, SyntaxError):
        return False
