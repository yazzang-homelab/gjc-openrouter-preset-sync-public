# GJC OpenRouter Preset Sync

- **Problem:** Keep GJC model presets current without hand-editing model lists.
- **Input:** OpenRouter usage data, your registered models, and machine-graded quality evidence.
- **Output:** A five-role primary/fallback profile, previewed first and applied only on request.

Languages: **English** (this file) · [한국어](README.ko.md)

## Quickstart

Requirements: Python >= 3.10, PyYAML >= 6.0.2 and < 7, a [GJC](https://github.com/Yeachan-Heo/gajae-code)
installation with at least one user profile that already references registered models (see
[GJC model profiles](https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/models.md)), and Linux
(recommended for sync; required for the evaluator, which uses `fcntl`, `getuid` and bubblewrap).
systemd is an optional Linux scheduler, not a dependency.

```bash
git clone https://github.com/yazzang-homelab/gjc-openrouter-preset-sync-public.git
cd gjc-openrouter-preset-sync-public
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python scripts/install.py
~/.local/bin/gjc-preset-sync --help
~/.local/bin/gjc-preset-sync init-policy
```

What just happened, and what did not:

- The installer copied the CLI wrapper to `~/.local/bin/gjc-preset-sync`; the package,
  `README.md`, `README.ko.md` and `LICENSE` to `~/.local/share/gjc-preset-sync`; the skill to
  `<agent-dir>/skills/openrouter-preset-sync/SKILL.md`, and two systemd **user** unit files.
  It installed no dependency, fetched no data, changed no preset, and enabled no timer.
- The wrapper runs the interpreter that executed `install.py`. Keep the virtual environment.
- `GJC_CODING_AGENT_DIR` or `install.py --agent-dir` selects the GJC user directory
  (default `~/.gjc/agent`). The skill becomes available as `/skill:openrouter-preset-sync`
  in a **new** GJC session with user skills enabled.
- `init-policy` wrote `~/.config/gjc-preset-sync/policy.json` containing only the
  `provider/model[:effort]` selectors your existing profiles already reference **and** that are
  registered in `models.yml`. It reads no auth files and never overwrites an existing policy.

**A successful install is not a ready-to-apply state.** The generated policy is a
non-executable draft (`"quality": {"status": "draft"}`). `plan` reports `policy_unconfigured`
and `sync --apply` is blocked until you complete the two steps below.

## Step 1 — Configure quality

Edit `~/.config/gjc-preset-sync/policy.json`. Keep the file out of Git; it is user
configuration. Two groups of fields matter.

**Discovery** (which models may be considered):

| Field | Meaning |
|---|---|
| `profile_id` | Name of the managed `or-*` profile. Aborts if it collides with a profile you own by hand |
| `allowed_selectors` | Exact allow-list of existing local `provider/model[:effort]` selectors |
| `aliases` | Exact local `provider/model` → exact OpenRouter ID. Not a substitution mechanism |
| `metric` | `request_share` (default) or `token_share` |
| `roles.*.tasks` | Observed task tags or patterns such as `code:*` with positive weights |
| `roles.*.top_k` | Primary plus fallbacks per role, 1–8 |
| `roles.*.effort` | Optional. Applied only when local thinking metadata confirms support |
| `filters.require_tools` | Requires OpenRouter tool support and no local incompatibility flag |
| `filters.min_context` | Both the catalog and the local `contextWindow` must satisfy it |
| `filters.max_prompt_per_million` / `filters.max_completion_per_million` | OpenRouter reference list prices, USD per million tokens |
| `cache_hours` / `max_age_hours` | Dataset cache (default 6 h) / maximum source age (default 72 h) |

**Quality** (what counts as confirmed). Replace the draft object with all of the following;
the validator rejects a partial object and treats only the exact draft as "unconfigured":

- `required_identity_level: "gjc_reported"`, `runtime_hash`, `runtime_version`, `harness_hash`
- `connections` — one entry per exact `provider/model` with `revision` and `config_hash`
- `quota` — `period_seconds`, `max_launches_per_period`, `max_new_candidates_per_period`,
  `max_reserved_wall_seconds_per_period`, `reevaluation_cooldown_seconds` (each an integer >= 1)
- `roles` — all five (`default`, `executor`, `planner`, `architect`, `critic`), each with
  `coverage`, `suite_hash`, `scorer_hash`, `conditions_hash`, `confirm_cases`, `screen_cases`,
  `shortlist`, `min_cases`, `max_failures`, `max_timeouts`, `min_pass_rate`,
  `evidence_ttl_hours`, `max_pair_gap_hours`, and `paired_rule` with `min_improvement`,
  `max_failure_increase`, `fallback_degradation`

Supplying every field makes the policy *configured*. It does not make the thresholds
statistically validated; you choose them. There is no configuration generator; the hash
fields are derived with the functions listed under [Derived values](#derived-values).

## Step 2 — Produce confirmation evidence

Popularity discovers candidates; only confirmation evidence can authorize a model into the
profile. Every one of the five roles and every primary/fallback needs fresh, valid evidence.

Evaluation is a separate CLI and is never started by `plan`, `sync`, the installer or the timer:

```bash
.venv/bin/python -m gjc_preset_sync.evaluate prepare \
  --manifest /private/manifest.json --policy ~/.config/gjc-preset-sync/policy.json \
  --models ~/.gjc/agent/models.yml --output /private/eval-out
```

`prepare` executes no model. It reports whether valid evidence can be reused, how many
launches a new run would need, and the remaining per-period quota. Use the interpreter that
has the package installed (`.venv/bin/python` above); a bare `python3` may select a different
environment unless you activate the virtual environment. With the file-copy installer alone, use that installation's
Python with `PYTHONPATH="$HOME/.local/share/gjc-preset-sync"`.

When `prepare` shows that new execution is required and you accept the cost:

```bash
.venv/bin/python -m gjc_preset_sync.evaluate run \
  --manifest /private/manifest.json --policy ~/.config/gjc-preset-sync/policy.json \
  --models ~/.gjc/agent/models.yml --approval /private/approval.json --output /private/eval-out
```

`run` reuses valid evidence first, screens a bounded shortlist, and confirms only the role
named in the manifest. It requires a manifest-bound approval file (owner-only `0600`), a
pinned self-contained GJC executable, a dedicated single-provider/single-model connection
directory, and bubblewrap. Persistent per-period launch/time/new-candidate quotas survive
restarts and new approval IDs. Screening results never count as promotion evidence.
Launch and time limits are **not** a monetary cap; an optional observed-cost soft stop cannot
guarantee one either. The 11 confirmation and 5 screening tasks measure bounded artifact
coverage, not general model quality, and no LLM judge is involved.

Evidence is stored under `~/.local/state/gjc-preset-sync/evaluation-state`. Manifest and
approval fields are listed under [Advanced details](#advanced-details).

## Step 3 — Preview, then apply explicitly

With `OPENROUTER_API_KEY` set in the current shell (only the task dataset endpoint needs it):

```bash
~/.local/bin/gjc-preset-sync tasks
~/.local/bin/gjc-preset-sync plan
```

`tasks` prints the task tags actually observed in the dataset so you can adjust
`roles.*.tasks`. `plan` performs `GET` requests, writes a private cache and
`~/.local/state/gjc-preset-sync/last-report.json`, and prints the profile it *would* write.
Neither command modifies `models.yml`.

When the plan matches your expectation:

```bash
~/.local/bin/gjc-preset-sync sync --apply
~/.local/bin/gjc-preset-sync status
gjc --mpreset or-auto
```

- The very first apply for a profile that does not exist yet must add `--bootstrap`. It does
  not skip any quality check; it only allows a baseline without an incumbent to compare against.
- `sync --apply` writes only the managed `or-*` profile. `config.yml`, your default selection,
  other profiles, and running sessions are untouched. The tool never calls `gjc --default`.
- If any role has no confirmed candidate, the whole existing profile is kept; there is no
  partial update.

## Optional — Scheduled refresh (Linux, systemd user timer)

The timer only refreshes data and reapplies under the same quality gate. It never runs the
evaluator; when evidence expires, the current profile is preserved.

Enable it only after a successful live `plan`/`sync --apply`. The service reads the key from
`~/.config/gjc-preset-sync/openrouter.env` (format `OPENROUTER_API_KEY=...`, mode `0600`);
shell environment variables are not inherited by the systemd user manager.

```bash
systemctl --user daemon-reload
systemctl --user enable --now gjc-preset-sync.timer
systemctl --user list-timers gjc-preset-sync.timer
journalctl --user -u gjc-preset-sync.service -n 30 --no-pager
```

Schedule: 00:17 / 06:17 / 12:17 / 18:17 Asia/Seoul with up to 120 s random delay
(edit the installed unit to change it). Whether the timer runs after logout depends on your
user-manager/linger settings. The timer never pulls repository code.

## Rollback

```bash
~/.local/bin/gjc-preset-sync rollback --apply
systemctl --user disable --now gjc-preset-sync.timer
```

Rollback undoes only the last managed change to the tool-owned profile and must itself pass
the current quality check; other profiles and providers edited since are not overwritten. If short model IDs were mapped without aliases, also pass
`--catalog-file <catalog.json>`. It aborts if another process edited the managed profile.
Backups (last 10) live in `~/.local/state/gjc-preset-sync` (`0700`/`0600`) and may contain
secrets from your full `models.yml` — do not share or commit them.

## Troubleshooting

| Symptom | Cause / action |
|---|---|
| `Python >=3.10 and PyYAML >=6.0.2 are required.` from the installer | Install PyYAML in the interpreter you use to run `install.py` (e.g. `.venv/bin/pip install .`). Nothing is installed automatically |
| `Refusing to overwrite an unowned installation` / `unowned file` | A path the installer would write is not marked as owned by this tool. Move it aside yourself; the installer never overwrites foreign files |
| `Policy already exists; it was not overwritten` | `init-policy` is one-shot. Edit or remove the existing policy by hand |
| `{"status": "policy_unconfigured"}` | `quality` is still the draft. Complete [Step 1](#step-1--configure-quality) |
| `Set OPENROUTER_API_KEY; no inference call is required` | Export the key in the current shell (or `openrouter.env` for the timer) |
| `Cold start requires explicit --bootstrap and confirmation evidence for all five roles` | First apply of a new profile: add `--bootstrap` **and** have confirmation evidence for all roles |
| `Incumbent confirmation is missing or invalid; retaining the entire preset` | The current primary lacks valid evidence. Re-run evaluation or accept the retained profile |
| `Policy or evidence changed during planning` | Files changed between plan and write. Re-run `plan`, then `sync --apply` |
| `--apply is valid only for sync and rollback` | `plan` and `tasks` are read-only; drop `--apply` |
| `--tasks-file and --catalog-file must be supplied together` / `Snapshot application requires --allow-snapshot-apply` | Offline/imported data needs both files and the extra apply gate |
| `New execution requires --approval` / `{"status": "blocked", ...}` from `evaluate` | Reuse was not possible and the approval, quota, manifest or runtime binding is missing or mismatched. Fix the input; there is no automatic retry |
| `Live evaluation requires bubblewrap` | Install `bwrap` on Linux. The evaluator refuses to run unsandboxed and does not fall back to a test runner |
| Timer runs but nothing changes | Expected when evidence expired or data is stale; the profile is preserved. See `journalctl --user -u gjc-preset-sync.service` and `last-report.json` |
| Stale `<models.yml>.lock` | The tool participates in GJC's lock and never breaks another owner's lock. Identify the owning process first |
| Errors mention `details suppressed to protect secrets` | Filesystem or input error; check paths and permissions (`0600`/`0700`) rather than expecting the tool to print contents |

## Advanced details

<details>
<summary>Data semantics, GJC integration, evaluation policy, manifest/approval fields, derived values, write safety, spend snapshots, tests, references</summary>

### What the data means

The implementation uses the official Data API `GET /api/v1/classifications/task?window=7d`,
which lists top models per task with `tag_usage_share` and `tag_token_share`, and the model
catalog. It is **not** a prompt classifier and **not** the Auto Router's internal
spend-share ranking. `request_share` is the default; `token_share` is available but is never
relabelled as spend. No `max_tokens: 1` probes, paid classifier calls, or scraping. Whether the
Data API itself is billed is not asserted; using the resulting profile in GJC is subject to
your existing connections' limits and prices.

Per-role discovery scores are share-weighted averages; new-evaluation shortlists require a
positive share. Among quality-confirmed candidates, confirmation pass rate is ranked first and
share breaks ties only. A valid incumbent is retained even if it drops out of the popular
top-N. No claim is made that popular models are better.

Mapping is accepted only when the full OpenRouter ID matches or the local model ID matches
OpenRouter's vendor-stripped ID **uniquely**; otherwise `aliases` are required. No fuzzy names,
version swaps, or wire-compatibility guesses. Effort variants of the same transport/model are
not added as duplicate fallbacks. Newly released models are never adopted unless registered
locally and allow-listed by you.

The default role policy is default = agent + code generation, executor = code generation,
planner = agent, architect = all `code:*`, critic = debugging. This is local policy, not
OpenRouter's role judgement.

### GJC integration

The tool targets GJC's user `profiles` / `model_mapping` schema; see `models-config-schema.ts`
below and re-check against the schema of the GJC version you run.

```yaml
profiles:
  or-auto:
    required_providers: []
    display_name: or-auto
    model_mapping:
      default: ["local/alpha:high", "local/beta:high"]
      executor: ["local/beta:high", "local/alpha:high"]
      planner: ["local/alpha:high", "local/beta:high"]
      architect: ["local/alpha:high", "local/beta:high"]
      critic: ["local/beta:high", "local/alpha:high"]
```

The model names above are fictional and illustrate the format only. `required_providers: []`
avoids forcing simultaneous authentication of every candidate account; GJC resolves each
selector's availability at runtime. Arrays use GJC's own primary/fallback mechanism; no new
provider is created.

### Evaluation policy in detail

- A new model release is not an execution permission. Only a bounded per-role shortlist that
  passed registration/allow-list/capability filters is considered.
- Valid observations with the same connection, model, requested effort, GJC executable, task
  set, scorer, and isolation conditions are reused. Changing only thresholds re-judges without
  launches and does not refresh collection time. Valid FAILs are reused too; failed cases are
  not selectively retried to swap in a pass.
- One screening task per role, then confirmation: executor 2, critic 3, default 2, planner 2,
  architect 2 cases. All machine-graded; screening FAIL/UNKNOWN means zero confirmation runs.
- Only the target role of a candidate is evaluated; comparable incumbent and other-role
  evidence is reused.
- Quotas count GJC launches, not HTTP requests or money. Unclear interruptions are counted
  conservatively. New approvals, run IDs, or restarts do not reset usage.
- `sync --evidence-dir` only changes where existing evidence is read from; it never evaluates.
- Live runs require bubblewrap and a hash-pinned, self-contained GJC binary (a hash-pinned
  shell wrapper is rejected). The connection directory may contain exactly one provider/model
  `models.yml` and an optional `auth.json`; the operator HOME is never copied. The grader runs in
  a separate no-network sandbox. bubblewrap is not a destination allow-list; provider egress
  restrictions are the environment's job. If isolation is unavailable, live execution is
  refused rather than downgraded to the test runner.
- Requested effort and GJC-reported model identity are what the tool binds to; they are not a
  backend attestation.

### Manifest fields

`version: 1`, unique `run_id`, `role`, `stage: screen|confirm`, exact `selector` and
`remote_id`, `binding`, full `cases`, `limits`
(`timeout_seconds`, `memory_bytes`, `cpu_seconds`, `processes`, `file_bytes`, `output_bytes`),
`run_timeout_seconds`, `runtime_path`, `agent_dir`, and read-only
`discovery_tasks` / `discovery_catalog`.

### Approval fields

`version: 1`, `approval_id`, `manifest_sha256` of the complete manifest, `selector`,
`connection_revision`, `role`, `stage`, `max_launches`, `case_timeout_seconds`,
`run_timeout_seconds`, timezone-aware `valid_until`, `approved: true`,
`acknowledge_no_monetary_cap: true`. Optional `observed_cost_soft_stop`:
`{"currency": "USD", "amount": <positive>}`. The approval cannot change the state root.

### Derived values

Import `quality`, `eval_suite`, and `evaluate` from `gjc_preset_sync`. The following
are Python API calls using your loaded model and policy objects, not shell commands:

| Field | Source |
|---|---|
| `connections.*.config_hash` | `quality.config_hash(models, provider, model)` |
| `binding` | `quality.expected_binding(models, policy["quality"], role, selector, base, remote_id, effort, stage)` |
| `cases` | `eval_suite.cases(role, stage)`; use `"screen"` or `"confirm"` for `stage` |
| `suite_hash`, `scorer_hash` | `eval_suite.suite_hash()`, `eval_suite.scorer_hash()` |
| `conditions_hash` | `evaluate.execution_conditions(limits, run_timeout_seconds)` |
| `harness_hash` | `quality.harness_hash()` |
| `manifest_sha256` | `quality.sha(manifest)`; hashes canonical JSON, not the formatted file bytes |

Changing the runner, parser, sandbox implementation, or run time limit invalidates existing
observations but does not start automatic re-evaluation. Never copy real keys or operational
data into public examples.

### Write safety

The tool participates in GJC's `<models.yml>.lock/info` locking, re-reads the original just
before writing, uses fsync + atomic rename, and recovers from a pending journal. Sections other
than `profiles` are preserved byte-for-byte; unmanaged profiles are preserved semantically.
Comments and formatting inside `profiles` may be moved or lost by PyYAML serialization, which
is why a backup is always taken first. Anchors, aliases, duplicate keys, and top-level
flow-style YAML are rejected rather than risk a wrong edit. A third-party editor that ignores
GJC's lock could still race in the very short check-then-rename window.

### Importing an operator-verified spend snapshot

Usage/token shares are never computed into or relabelled as spend. Only with a separately
verified snapshot (format: `examples/spend-snapshot.schema-example.json`, fictional values):

```bash
gjc-preset-sync plan --tasks-file /private/spend.json --catalog-file /private/models.json --spend-snapshot
gjc-preset-sync sync --apply --tasks-file /private/spend.json --catalog-file /private/models.json --spend-snapshot --allow-snapshot-apply
```

The operator vouches for provenance; the tool does not verify OpenRouter signatures and ships
no automatic spend collector.

### Tests and public bundle

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_site.py
```

Tests are offline and use fictional models in isolated temporary directories; passing them
says nothing about your live installation. `build_site.py` writes `dist/site/` (static intro
page, `source.zip`, `SHA256SUMS`) from an explicit allow-list only. Checksums verify file
identity, not publisher identity; the build does not publish anything.

### References

- Auto Router: https://openrouter.ai/docs/guides/routing/routers/auto-router
- Task Data API, schema, license: https://openrouter.ai/docs/cookbook/administration/data-api
- Public task-spend page: https://openrouter.ai/rankings#task-spend
- GJC model profiles / fallback arrays: https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/models.md
- GJC profile schema: https://github.com/Yeachan-Heo/gajae-code/blob/main/packages/coding-agent/src/config/models-config-schema.ts

</details>

## Distribution and licenses

This distribution contains source code and fictional test fixtures — no user configuration,
credentials, operational reports, or private Git history.

- **Software:** MIT License, see [LICENSE](LICENSE).
- **Data:** the OpenRouter dataset consumed at runtime is licensed by OpenRouter under
  CC BY 4.0. Generated reports keep the source date and attribution. That attribution refers to
  the data, not to this code.
