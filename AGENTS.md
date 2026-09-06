# Repository contract

- Public distribution source only. Never add user models.yml, auth stores, keys, environment dumps, cached datasets, backups, or private repository history.
- Read-only data APIs only. No chat/completions, paid classifier probes, provider/account switching, or new inference backend.
- The separately invoked `gjc_preset_sync.evaluate run` may execute the existing GJC CLI for machine-graded tasks with explicit per-run approval and persistent launch/time quotas. Synchronization, installation, and timers must never invoke evaluation. This exception does not permit classifier probes, a new inference backend, or account/provider switching.
- Reuse valid exact-configuration evidence before evaluating shortlisted candidates. Screening cannot authorize promotion; all five roles and every primary/fallback require confirmation evidence. Launch/time limits do not guarantee a monetary cap.
- Do not confuse request/token shares with spend shares, or popularity with quality.
- GJC integration is through tool-owned user `profiles` entries; preserve defaults and unrelated configuration.
- Exact model identity only. Fail closed on stale/ambiguous/incompatible data; never manufacture candidates.
- Run `python3 -m unittest discover -s tests -v` before committing.
- Tests must be offline and use temporary isolated models.yml files.
- Installation, live-source validation, applying a managed profile, selecting it as default, and enabling a timer are separate states. Report each honestly.
- Do not enable scheduling until live validation succeeds and the operator requests scheduling.
