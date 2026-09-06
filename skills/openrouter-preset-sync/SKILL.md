---
name: openrouter-preset-sync
description: >
  OpenRouter 작업별 모델 점유율을 GJC model preset으로 갱신한다.
  "프리셋 자동 업데이트", "OpenRouter 순위 반영", "gjc preset sync",
  "모델 후보 재정렬", "/skill:openrouter-preset-sync" 요청에 사용한다.
  추론 API를 호출하지 않으며 기존 기본 모델과 사용자 프리셋을 임의로 변경하지 않는다.
---

# OpenRouter preset sync

## Contract

Use the installed `~/.local/bin/gjc-preset-sync` CLI. This file is a procedure,
not a grant to spend money, read arbitrary credentials, or change defaults.
Read `~/.local/share/gjc-preset-sync/README.ko.md` for installation and policy details.

**Do not call chat/completions for classification, not even max_tokens=1.**
No paid-model fallback, new provider, account switch, or prompt upload is part of this skill.
The updater uses GET task classifications and GET model catalog only.
Do not print environment variables, authentication files, or raw provider configuration.

## Procedure

1. Inspect `gjc-preset-sync status` and the non-secret policy at
   `~/.config/gjc-preset-sync/policy.json`. If the policy is absent, run
   `gjc-preset-sync init-policy`: this only imports existing registered pins
   from user profiles. It never modifies `models.yml` or reads auth stores.
2. Run `gjc-preset-sync plan`. It is a model-configuration dry run, but can make
   GET requests and write the private dataset cache/report. Explain source
   date, metric, rejected mappings, and selected candidates. Check
   `~/.local/state/gjc-preset-sync/last-report.json` for details.
3. On an explicit apply/update request, run `gjc-preset-sync sync --apply`.
   It changes only tool-owned `or-*` profiles in `models.yml`. Never pass
   `gjc --default` or change `config.yml` on the user's behalf without a
   separate request. The installed preset applies to new sessions started
   with `gjc --mpreset or-auto`, not automatically to an already-running session.
4. Scheduled operation is provided by the installed **systemd user timer**,
   not by this Markdown file. Only enable it after a successful live plan/apply
   and an explicit request for scheduled updates. Use `systemctl --user`.
   Never copy a secret from another application to make the timer work.
5. On failure, report the actual safe error and leave the last preset intact.
   Use `gjc-preset-sync rollback --apply` only when asked to undo the last
   managed change. Do not restore a whole backup over unrelated user edits.

## Meaning of the data

`request_share` and `token_share` are public top-N shares from OpenRouter's
trailing-seven-day task dataset. They are **not** its internal spend-share
ranking, a quality benchmark, or the output of its prompt classifier.
Role-to-task weights are local policy, not OpenRouter's classification.
`gjc-preset-sync tasks` shows the actually observed tags. Never invent a missing
tag or use a general popularity list as though it were a task-specific rank.

An operator-provided `spend_share` snapshot can be imported explicitly. Do not
create, fabricate, or silently refresh it from usage shares. Offline fixtures
must never be applied to the user's production config. Tests use isolated homes.

Aliases are exact local selector -> exact OpenRouter ID mappings. Do not use
substring/fuzzy substitutions, cross-model equivalence, or account credentials
to "fix" a missing mapping. No eligible candidate means no update.
Price limits refer to OpenRouter list prices, not the user's subscription bill.

## Report

State separately: repository implementation, installed skill, live-data verification,
profile application, default/session selection, and timer enablement. Never claim
all are complete because only tests passed. Include attribution when quoting data.
