# GJC OpenRouter Preset Sync

GJC skill/CLI that updates policy-owned model profiles from OpenRouter task data.

**Synchronization makes no inference calls. No automatic default-model change.**
The documented API's request/token shares are not presented as the Auto Router's internal spend ranking.

Policy v2 requires fresh, configuration-bound confirmation evidence for all five roles
and every primary/fallback. Popularity discovers candidates; it cannot authorize promotion.
`init-policy` creates a non-executable draft, not a ready-to-apply policy. Version 1 is rejected.

The separate `python3 -m gjc_preset_sync.evaluate` CLI offers `prepare` (no model execution)
and explicitly approved `run`. It reuses valid evidence, screens a bounded shortlist, and
confirms only the required role. Persistent per-period launch/time/new-candidate quotas
survive restarts and new approval IDs. Screening is not promotion evidence. Timers never
run evaluations. Live execution requires a pinned self-contained GJC executable, a dedicated
single-provider/single-model connection directory and bubblewrap; tests use isolated synthetic
subprocesses. Requested effort and GJC-reported model identity are not backend attestation.
Launch/time limits and optional observed-cost soft stops do **not** guarantee a monetary cap.
The 11 confirmation and 5 screening tasks measure bounded artifact coverage, not general
model quality. No LLM judge is used, and no measured cost-saving percentage is claimed.

See the [한국어 설치·운영 가이드](README.ko.md) for installation, validation, and limitations.

This distribution contains source code and fictional test fixtures, not user configuration,
credentials, operational reports, or private Git history. No software license has been
selected for this distribution; public visibility does not itself grant a software license.
The CC BY 4.0 attribution in generated reports refers to the OpenRouter dataset, not this code.
