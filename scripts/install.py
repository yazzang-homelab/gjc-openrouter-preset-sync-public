#!/usr/bin/env python3
"""Install the user skill/CLI. Does not fetch data, enable timers, or change GJC defaults."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

OWNER = "gjc-openrouter-preset-sync"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--agent-dir", type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 10) or importlib.util.find_spec("yaml") is None:
        raise SystemExit("Python >=3.10 and PyYAML >=6.0.2 are required. No dependency was installed automatically.")
    repo = Path(__file__).resolve().parent.parent
    home = args.home.absolute()
    agent = args.agent_dir or Path(os.environ.get("GJC_CODING_AGENT_DIR", home / ".gjc/agent"))
    share = home / ".local/share/gjc-preset-sync"
    skill = agent / "skills/openrouter-preset-sync"
    wrapper = home / ".local/bin/gjc-preset-sync"
    unitdir = home / ".config/systemd/user"
    units = [unitdir / f"gjc-preset-sync.{kind}" for kind in ("service", "timer")]
    for target in (share, skill):
        marker = target / ".installer-owner"
        if target.is_symlink() or (target.exists() and (not marker.is_file() or marker.read_text().strip() != OWNER)):
            raise SystemExit(f"Refusing to overwrite an unowned installation: {target}")
    for target in (wrapper, *units):
        if target.is_symlink() or (target.exists() and f"# owner: {OWNER}" not in target.read_text()):
            raise SystemExit(f"Refusing to overwrite an unowned file: {target}")
    for target in (share, skill, wrapper.parent, unitdir):
        target.mkdir(parents=True, exist_ok=True)
    for target in (share, skill):
        (target / ".installer-owner").write_text(OWNER + "\n")
    shutil.copytree(repo / "gjc_preset_sync", share / "gjc_preset_sync", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(repo / "skills/openrouter-preset-sync/SKILL.md", skill / "SKILL.md")
    for doc in ("README.md", "README.ko.md", "LICENSE"):
        shutil.copy2(repo / doc, share / doc)
    wrapper.write_text("#!/bin/sh\n# owner: " + OWNER + "\n" +
                       "export PYTHONPATH=" + shlex.quote(str(share)) + "\n" +
                       "exec " + shlex.quote(sys.executable) + ' -m gjc_preset_sync "$@"\n')
    wrapper.chmod(0o755)
    for unit in units:
        text = (repo / "systemd" / unit.name).read_text()
        # A custom agent directory is installed into the service explicitly and safely.
        if agent.absolute() != home / ".gjc/agent":
            if any(ch in str(agent) for ch in ('\n', '\r', '"', '%')):
                raise SystemExit("Custom agent path is not safe for a systemd Environment entry")
            text = text.replace("[Service]\n", '[Service]\nEnvironment="GJC_CODING_AGENT_DIR=' + str(agent.absolute()) + '"\n')
        unit.write_text(text)
    print(json.dumps({"installed_cli": str(wrapper), "installed_skill": str(skill / "SKILL.md"),
                      "timer_files_installed": True, "timer_enabled": False,
                      "models_changed": False, "network_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
