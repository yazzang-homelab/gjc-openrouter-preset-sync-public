# Repository contract

- Public distribution source only. Never add user models.yml, auth stores, keys, environment dumps, cached datasets, backups, or private repository history.
- Read-only data APIs only. No chat/completions, paid classifier probes, provider/account switching, or new inference backend.
- Do not confuse request/token shares with spend shares, or popularity with quality.
- GJC integration is through tool-owned user `profiles` entries; preserve defaults and unrelated configuration.
- Exact model identity only. Fail closed on stale/ambiguous/incompatible data; never manufacture candidates.
- Run `python3 -m unittest discover -s tests -v` before committing.
- Tests must be offline and use temporary isolated models.yml files.
- Installation, live-source validation, applying a managed profile, selecting it as default, and enabling a timer are separate states. Report each honestly.
- Do not enable scheduling until live validation succeeds and the operator requests scheduling.
