"""Policy-bound OpenRouter ranking -> GJC profile sync. Never performs inference."""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re

import tempfile
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Iterator

import yaml

from . import quality

VERSION = "0.1.0"
MAX_BYTES = 8 * 1024 * 1024
ROLES = ("default", "executor", "planner", "architect", "critic")
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
URLS = {
    "tasks": "https://openrouter.ai/api/v1/classifications/task?window=7d",
    "models": "https://openrouter.ai/api/v1/models",
}
SOURCE = "https://openrouter.ai/rankings"


class SyncError(Exception):
    """A safe, actionable error: messages must not contain credentials or raw HTTP bodies."""


class UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise SyncError("YAML requires unique string keys; duplicate/merge keys are not supported")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def decode_yaml(text: str) -> dict:
    try:
        # Aliases can give one node several semantic owners; never mutate such a document.
        for token in yaml.scan(text):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise SyncError("YAML anchors/aliases are unsupported; no configuration was changed")
        value = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError:
        raise SyncError("Invalid YAML; details suppressed to protect credentials") from None
    if not isinstance(value, dict):
        raise SyncError("Expected a YAML mapping")
    return value


def read_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise SyncError("Refusing a symlink file")
    try:
        with path.open("rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError:
        raise SyncError(f"Cannot read required file: {path.name}") from None
    if len(data) > MAX_BYTES:
        raise SyncError("Input exceeds size limit")
    return data


def read_json(path: Path) -> dict:
    try:
        result = json.loads(read_bytes(path))
    except (ValueError, UnicodeError):
        raise SyncError(f"Invalid JSON: {path.name}") from None
    if not isinstance(result, dict):
        raise SyncError("Expected a JSON object")
    return result


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise SyncError("Refusing a symlink state directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise SyncError("State directory is owned by another user")
    os.chmod(path, 0o700)


def atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise SyncError("Refusing a symlink destination")
    fd, tmp = tempfile.mkstemp(prefix=".sync-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def gjc_lock(models_path: Path) -> Iterator[None]:
    """Cooperate with GJC's <file>.lock/info contract. Never reclaim another lock."""
    lock = Path(str(models_path) + ".lock")
    token = uuid.uuid4().hex
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise SyncError("GJC models.yml lock exists; retry after its owner finishes") from None
    identity = lock.stat().st_ino
    try:
        atomic_write(lock / "info", json_bytes({
            "pid": os.getpid(), "start_time": "unknown", "timestamp": int(time.time() * 1000),
            "owner_token": token,
        }))
        yield
    finally:
        if not lock.is_symlink() and lock.exists() and lock.stat().st_ino == identity:
            info = read_json(lock / "info") if (lock / "info").exists() else {}
            if info.get("owner_token") == token:
                (lock / "info").unlink()
                lock.rmdir()


def patch_profiles(text: str, changes: dict[str, dict | None]) -> str:
    """Rewrite only the top-level profiles section. Preserve other sections byte-for-byte."""
    original = decode_yaml(text)
    profiles = copy.deepcopy(original.get("profiles") or {})
    if not isinstance(profiles, dict):
        raise SyncError("profiles must be a mapping")
    for name, value in changes.items():
        if value is None:
            profiles.pop(name, None)
        else:
            profiles[name] = value
    root = yaml.compose(text, Loader=UniqueLoader)
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise SyncError("Top-level YAML must use block style")
    replacement = yaml.safe_dump({"profiles": profiles}, sort_keys=False, allow_unicode=True)
    pair_index = next((i for i, (k, _) in enumerate(root.value) if k.value == "profiles"), None)
    if pair_index is None:
        result = text.rstrip("\n") + "\n\n" + replacement
    else:
        key, _ = root.value[pair_index]
        end = root.value[pair_index + 1][0].start_mark.index if pair_index + 1 < len(root.value) else len(text)
        result = text[:key.start_mark.index] + replacement + text[end:]
    verified = decode_yaml(result)
    expected = copy.deepcopy(original)
    expected["profiles"] = profiles
    if verified != expected:
        raise SyncError("Round-trip verification failed; no write performed")
    return result


def _number(value: Any, label: str, lo: float = 0, hi: float = float("inf")) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise SyncError(f"Expected finite number for {label}")
    if not lo <= value <= hi:
        raise SyncError(f"Out-of-range {label}")
    return float(value)


def age_check(as_of: str, max_age_hours: float, now: dt.datetime | None = None) -> None:
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        timestamp = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError, AttributeError):
        raise SyncError("Invalid source as_of timestamp") from None
    age = (now - timestamp).total_seconds() / 3600
    if age < -1 or age > max_age_hours:
        raise SyncError("Ranking snapshot is stale or future-dated; retaining installed presets")


def normalize_tasks(payload: dict, metric: str, max_age_hours: float = 72,
                    now: dt.datetime | None = None) -> dict:
    fields = {"request_share": "tag_usage_share", "token_share": "tag_token_share"}
    if metric not in fields:
        raise SyncError("Official API supports request_share or token_share here, not spend_share")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("window_days") != 7:
        raise SyncError("Unexpected task API schema/window")
    age_check(data.get("as_of"), max_age_hours, now)
    tasks = {}
    rows = data.get("classifications")
    if not isinstance(rows, list) or not rows:
        raise SyncError("Task classifications are missing/empty")
    for task in rows:
        if not isinstance(task, dict):
            raise SyncError("Malformed task classification")
        tag = task.get("tag")
        if not isinstance(tag, str) or not re.fullmatch(r"[a-z0-9_]+:[a-z0-9_]+", tag) or tag in tasks:
            raise SyncError("Invalid/duplicate task tag")
        shares = {}
        if not isinstance(task.get("models"), list):
            raise SyncError("Missing task model list")
        for model in task["models"]:
            if not isinstance(model, dict):
                raise SyncError("Malformed task model")
            model_id = model.get("id")
            if not isinstance(model_id, str) or not valid_selector(model_id) or model_id in shares:
                raise SyncError("Invalid/duplicate ranked model ID")
            shares[model_id] = _number(model.get(fields[metric]), "model share", 0, 1)
        if sum(shares.values()) > 1.02:
            raise SyncError("Task shares exceed the documented fraction range")
        tasks[tag] = shares
    return {"as_of": data["as_of"], "window_days": 7, "metric": metric, "tasks": tasks,
            "source_url": SOURCE, "attribution": f"Source: OpenRouter (openrouter.ai/rankings), as of {data['as_of']}. Licensed under CC BY 4.0."}


def normalize_spend(payload: dict, max_age_hours: float = 72) -> dict:
    """Import a verified operator-provided snapshot. NOT an undocumented HTTP adapter."""
    if (payload.get("metric") != "spend_share" or payload.get("source_url") != SOURCE
            or payload.get("window_days") != 7 or not isinstance(payload.get("tasks"), dict)):
        raise SyncError("Spend snapshot must declare spend_share, the OpenRouter rankings source, and 7 days")
    age_check(payload.get("as_of"), max_age_hours)
    # Reuse exactly the same fraction/ID validation, without pretending these are request shares.
    surrogate = {"data": {"as_of": payload["as_of"], "window_days": 7, "classifications": [
        {"tag": tag, "models": [{"id": mid, "tag_usage_share": share} for mid, share in models.items()]}
        for tag, models in payload["tasks"].items() if isinstance(models, dict)
    ]}}
    if len(surrogate["data"]["classifications"]) != len(payload["tasks"]):
        raise SyncError("Malformed spend snapshot")
    result = normalize_tasks(surrogate, "request_share", max_age_hours)
    result["metric"] = "spend_share"
    return result


def valid_selector(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 256 and bool(re.fullmatch(r"[A-Za-z0-9_.:-]+/[A-Za-z0-9_./:+-]+", value))


def local_model(selector: str, models: dict) -> tuple[str, dict, str | None]:
    if not valid_selector(selector):
        raise SyncError("Invalid allowed model selector")
    provider, model_id = selector.split("/", 1)
    definition = (models.get("providers") or {}).get(provider, {})
    catalog = definition.get("models", [])
    # Exact model ID wins, including :free and colon-bearing local IDs.
    matches = [m for m in catalog if isinstance(m, dict) and m.get("id") == model_id]
    effort = None
    if not matches and ":" in model_id and model_id.rsplit(":", 1)[1] in EFFORTS:
        model_id, effort = model_id.rsplit(":", 1)
        matches = [m for m in catalog if isinstance(m, dict) and m.get("id") == model_id]
    if len(matches) != 1:
        raise SyncError("Allowed selector is not uniquely registered in models.yml")
    item = dict(matches[0])
    item["compat"] = {**(definition.get("compat") or {}), **(item.get("compat") or {})}
    return provider + "/" + model_id, item, effort


def validate_policy(policy: dict) -> None:
    if policy.get("version") != 2:
        raise SyncError("Policy version 1 is unsupported; create a version 2 draft with init-policy --policy <new-path>")
    try:
        quality.validate_policy(policy.get("quality"))
    except quality.QualityError as error:
        raise SyncError(str(error)) from None
    name = policy.get("profile_id")
    if not isinstance(name, str) or not re.fullmatch(r"or-[a-z0-9][a-z0-9._-]{0,59}", name):
        raise SyncError("Managed profile_id must begin with or- and use safe lowercase characters")
    allow = policy.get("allowed_selectors")
    if not isinstance(allow, list) or not allow or len(allow) > 256 or any(not valid_selector(s) for s in allow):
        raise SyncError("A non-empty exact allowed_selectors list is required")
    if len(set(allow)) != len(allow):
        raise SyncError("Duplicate allowed selectors")
    roles = policy.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise SyncError("Policy must specify all five GJC roles")
    for spec in roles.values():
        if not isinstance(spec, dict) or not isinstance(spec.get("tasks"), dict) or not spec["tasks"]:
            raise SyncError("Every role needs task weights")
        for pattern, weight in spec["tasks"].items():
            if not re.fullmatch(r"[a-z0-9_]+:[a-z0-9_*]+", pattern):
                raise SyncError("Invalid task pattern")
            _number(weight, "task weight", 0.000001, 100)
        k = spec.get("top_k", 3)
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 8:
            raise SyncError("top_k must be an integer between 1 and 8")
        if spec.get("effort") not in (*EFFORTS, None):
            raise SyncError("Unsupported role effort")
    for key in ("max_age_hours", "cache_hours"):
        _number(policy.get(key, 72 if key == "max_age_hours" else 6), key, 0.01, 168)
    filters = policy.get("filters", {})
    if not isinstance(filters, dict):
        raise SyncError("filters must be an object")
    for key in ("min_context", "max_prompt_per_million", "max_completion_per_million"):
        if filters.get(key) is not None:
            _number(filters[key], key)
    aliases = policy.get("aliases", {})
    if not isinstance(aliases, dict) or any(not valid_selector(k) or not valid_selector(v) for k, v in aliases.items()):
        raise SyncError("Aliases must map exact local selectors to exact OpenRouter IDs")


def _price(value: Any) -> float | None:
    try:
        number = float(value)
        return number * 1_000_000 if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def eligible(remote: dict, local: dict, filters: dict) -> bool:
    if filters.get("require_tools", True):
        if "tools" not in (remote.get("supported_parameters") or []):
            return False
        compat = local.get("compat") or {}
        if compat.get("toolChoiceSupport") == "none" or compat.get("supportsToolChoice") is False:
            return False
    minimum = filters.get("min_context", 0)
    if minimum:
        # Both the remote catalog and local transport cap must be known and sufficient.
        values = [remote.get("context_length"), local.get("contextWindow")]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < minimum for v in values):
            return False
    pricing = remote.get("pricing") or {}
    for field in ("prompt", "completion"):
        cap = filters.get(f"max_{field}_per_million")
        if cap is not None:
            price = _price(pricing.get(field))
            if price is None or price > cap:
                return False
    return True


def catalog_models(catalog_payload: dict) -> dict:
    rows = catalog_payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise SyncError("Model catalog is empty or malformed")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not valid_selector(row.get("id")) or row["id"] in result:
            raise SyncError("Invalid/duplicate catalog model ID")
        result[row["id"]] = row
    return result


def resolve_remote_id(base: str, local: dict, policy: dict, catalog: dict | None) -> str:
    explicit = policy.get("aliases", {}).get(base)
    if catalog is None:
        if explicit or valid_selector(local["id"]):
            return explicit or local["id"]
        raise SyncError("Exact short model mapping requires a catalog or explicit alias")
    matches = [mid for mid in catalog if mid == explicit] if explicit else [
        mid for mid in catalog if mid == local["id"] or mid.split("/", 1)[1] == local["id"]]
    if len(matches) != 1:
        raise SyncError("Model mapping is missing or ambiguous")
    return matches[0]


def evaluation_shortlist(models: dict, policy: dict, ranking: dict, catalog_payload: dict) -> dict:
    """Discovery only: bounded compatible candidates, never evaluation or promotion."""
    validate_policy(policy)
    if not quality.validate_policy(policy["quality"]):
        return {role: [] for role in ROLES}
    by_id = catalog_models(catalog_payload)
    result = {}
    for role, spec in policy["roles"].items():
        weights = {tag: max([w for p, w in spec["tasks"].items() if fnmatch.fnmatchcase(tag, p)] or [0])
                   for tag in ranking["tasks"]}
        total = sum(weights.values())
        candidates = []
        for index, selector in enumerate(policy["allowed_selectors"]):
            try:
                base, local, effort = local_model(selector, models)
            except SyncError:
                continue
            try:
                mid = resolve_remote_id(base, local, policy, by_id)
            except SyncError:
                continue
            if not eligible(by_id[mid], local, policy.get("filters", {})):
                continue
            requested = spec.get("effort") or effort
            thinking = local.get("thinking") or {}
            levels = thinking.get("levels")
            if not levels and thinking.get("minLevel") in EFFORTS and thinking.get("maxLevel") in EFFORTS:
                levels = EFFORTS[EFFORTS.index(thinking["minLevel"]):EFFORTS.index(thinking["maxLevel"]) + 1]
            if not requested or not levels or requested not in levels:
                continue
            score = sum(ranking["tasks"][tag].get(mid, 0) * weight for tag, weight in weights.items()) / total if total else 0
            if score > 0:
                candidates.append({"selector": base + ":" + requested, "remote_id": mid, "share": score, "order": index})
        candidates.sort(key=lambda c: (-c["share"], c["order"]))
        seen, unique = set(), []
        for candidate in candidates:
            if candidate["selector"] not in seen:
                unique.append(candidate)
                seen.add(candidate["selector"])
        result[role] = unique[:policy["quality"]["roles"][role]["shortlist"]]
    return result


def build_profile(models: dict, policy: dict, ranking: dict, catalog_payload: dict,
                  evidence: list[dict] = (), *, bootstrap: bool = False,
                  allow_test: bool = False) -> tuple[dict, dict]:
    validate_policy(policy)
    if not quality.validate_policy(policy["quality"]):
        raise SyncError("policy_unconfigured; explicitly configure quality thresholds and evaluation limits")
    incumbent_mapping = ((models.get("profiles") or {}).get(policy["profile_id"]) or {}).get("model_mapping", {})
    if not incumbent_mapping and not bootstrap:
        raise SyncError("Cold start requires explicit --bootstrap and confirmation evidence for all five roles")
    remote_by_id = catalog_models(catalog_payload)
    candidates, rejected = [], []
    for position, selector in enumerate(policy["allowed_selectors"]):
        try:
            base, local, existing_effort = local_model(selector, models)
        except SyncError:
            rejected.append({"selector": selector, "reason": "not_registered"})
            continue
        try:
            mid = resolve_remote_id(base, local, policy, remote_by_id)
        except SyncError:
            rejected.append({"selector": selector, "reason": "unmapped_or_ambiguous"})
            continue
        if not eligible(remote_by_id[mid], local, policy.get("filters", {})):
            rejected.append({"selector": selector, "reason": "capability_or_reference_price_filter"})
            continue
        candidates.append({"selector": selector, "base": base, "model_id": mid,
                           "effort": existing_effort, "local": local, "order": position})
    role_mapping, report_roles = {}, {}
    for role, spec in policy["roles"].items():
        weights = {tag: max([w for p, w in spec["tasks"].items() if fnmatch.fnmatchcase(tag, p)] or [0])
                   for tag in ranking["tasks"]}
        weights = {tag: w for tag, w in weights.items() if w > 0}
        if not weights:
            raise SyncError(f"No observed task tag matched role {role}; refusing a partial preset")
        total_weight = sum(weights.values())
        scored = []
        for candidate in candidates:
            mid = candidate["model_id"]
            score = sum(ranking["tasks"][tag].get(mid, 0) * w for tag, w in weights.items()) / total_weight
            selector = candidate["selector"]
            effort = spec.get("effort")
            if effort:
                thinking = candidate["local"].get("thinking") or {}
                levels = thinking.get("levels")
                if not levels and thinking.get("minLevel") in EFFORTS and thinking.get("maxLevel") in EFFORTS:
                    levels = EFFORTS[EFFORTS.index(thinking["minLevel"]):EFFORTS.index(thinking["maxLevel"]) + 1]
                if not levels or effort not in levels:
                    continue  # Unknown effort capabilities are not guessed.
                selector = candidate["base"] + ":" + effort
            pinned_effort = effort or candidate["effort"]
            if not pinned_effort:
                continue
            try:
                binding = quality.expected_binding(models, policy["quality"], role, selector,
                            candidate["base"], mid, pinned_effort)
            except quality.QualityError:
                continue
            spec_quality = policy["quality"]["roles"][role]
            matching = [row for row in evidence if row.get("binding") == binding]
            matching.sort(key=lambda row: row.get("started_at", ""), reverse=True)
            # Latest whole run, not the best score selected from prior attempts.
            observation = matching[0] if matching else None
            verdict = quality.assess(observation, binding, spec_quality, allow_test=allow_test)
            if verdict["verdict"] != "PASS":
                continue
            scored.append({"selector": selector, "base": candidate["base"], "openrouter_id": mid, "score": score,
                               "order": candidate["order"], "quality_rate": verdict["rate"],
                               "evidence": observation})
        scored.sort(key=lambda c: (-c["quality_rate"], -c["score"], c["order"]))
        if not scored:
            raise SyncError(f"No eligible ranked model for role {role}; retaining installed preset")
        old = incumbent_mapping.get(role, [])
        old = old if isinstance(old, list) else [old]
        by_selector = {c["selector"]: c for c in scored}
        incumbent = by_selector.get(old[0]) if old else None
        if not incumbent and not bootstrap:
            raise SyncError("Incumbent confirmation is missing or invalid; retaining the entire preset")
        if incumbent:
            primary = next((c for c in scored if c["selector"] != incumbent["selector"]
                            and quality.paired(c["evidence"], incumbent["evidence"], spec_quality,
                                               allow_test=allow_test)), incumbent)
            selected = [primary] + [c for c in scored if c is not primary and
                        (c["selector"] in old or quality.paired(c["evidence"], primary["evidence"],
                         spec_quality, fallback=True, allow_test=allow_test))]
        else:
            selected = scored
        unique, seen = [], set()
        for candidate in selected:
            if candidate["base"] not in seen:
                unique.append(candidate)
                seen.add(candidate["base"])
        selected = unique[:spec.get("top_k", 3)]
        role_mapping[role] = [c["selector"] for c in selected]
        report_roles[role] = {"task_weights": weights, "candidates": [
            {k: v for k, v in c.items() if k != "evidence"} for c in scored], "selected": role_mapping[role]}
    # No provider is made a hard credential prerequisite: GJC resolves the explicit fallback pins.
    profile = {"required_providers": [], "display_name": policy["profile_id"], "model_mapping": role_mapping}
    report = {"profile_id": policy["profile_id"], "metric": ranking["metric"], "as_of": ranking["as_of"],
              "source_url": ranking["source_url"], "attribution": ranking["attribution"],
              "ranking_semantics": "weighted share among published top-N; not a benchmark or official router replication",
              "roles": report_roles, "rejected": rejected, "inference_calls": 0}
    return profile, report


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SyncError("HTTP redirect refused; credentials were not forwarded")


def check_profile_quality(models: dict, policy: dict, profile: dict, evidence: list[dict],
                          *, allow_test: bool = False, catalog_payload: dict | None = None) -> None:
    """Absolute all-chain gate used inside the models transaction lock."""
    validate_policy(policy)
    if not quality.validate_policy(policy["quality"]):
        raise SyncError("policy_unconfigured")
    mapping = profile.get("model_mapping", {})
    if set(mapping) != set(ROLES):
        raise SyncError("Quality gate requires all five roles")
    for role, chain in mapping.items():
        if not isinstance(chain, list) or not chain:
            raise SyncError("Quality gate requires nonempty explicit chains")
        for selector in chain:
            base, local, effort = local_model(selector, models)
            if not effort:
                raise SyncError("Quality gate requires explicit effort")
            remote = resolve_remote_id(base, local, policy, catalog_models(catalog_payload) if catalog_payload else None)
            try:
                binding = quality.expected_binding(models, policy["quality"], role, selector, base, remote, effort)
            except quality.QualityError as error:
                raise SyncError(str(error)) from None
            matches = sorted([e for e in evidence if e.get("binding") == binding],
                             key=lambda e: e.get("started_at", ""), reverse=True)
            if not matches or quality.assess(matches[0], binding, policy["quality"]["roles"][role],
                                             allow_test=allow_test)["verdict"] != "PASS":
                raise SyncError("Quality confirmation missing, expired or failed; no profile written")


def fetch_json(which: str, api_key: str | None = None) -> dict:
    if which not in URLS:
        raise SyncError("Unknown data source")
    headers = {"Accept": "application/json", "User-Agent": f"gjc-preset-sync/{VERSION}"}
    if which == "tasks":
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise SyncError("Set OPENROUTER_API_KEY; no inference call is required")
        headers["Authorization"] = "Bearer " + api_key
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        request = urllib.request.Request(URLS[which], headers=headers, method="GET")
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise SyncError("HTTP response exceeds size limit")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise SyncError("Expected JSON object from data source")
                return value
        except urllib.error.HTTPError as error:
            if (error.code == 429 or 500 <= error.code < 600) and attempt < 2:
                try:
                    delay = min(max(float(error.headers.get("Retry-After", 2 ** attempt)), 1), 30)
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(delay)
                continue
            raise SyncError(f"OpenRouter {which} HTTP {error.code}; no response body logged") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise SyncError(f"OpenRouter {which} request failed; no secrets or response body logged") from None
    raise SyncError("Data fetch failed")


def get_dataset(policy: dict, state_dir: Path, refresh: bool = False) -> tuple[dict, dict, bool]:
    private_dir(state_dir)
    cache_file = state_dir / "data-cache.json"
    metric = policy.get("metric", "request_share")
    max_age = policy.get("max_age_hours", 72)
    if not refresh and cache_file.exists():
        cache = read_json(cache_file)
        fetched = cache.get("fetched_at_epoch", 0)
        age = time.time() - fetched if isinstance(fetched, (int, float)) else -1
        if 0 <= age < policy.get("cache_hours", 6) * 3600:
            try:
                return normalize_tasks(cache["tasks"], metric, max_age), cache["models"], True
            except (SyncError, KeyError):
                pass  # Stale/invalid cache is never applied.
    tasks = fetch_json("tasks", os.environ.get("OPENROUTER_API_KEY"))
    catalog = fetch_json("models")
    ranking = normalize_tasks(tasks, metric, max_age)
    if not isinstance(catalog.get("data"), list) or not catalog["data"]:
        raise SyncError("Catalog missing; cache not updated")
    atomic_write(cache_file, json_bytes({"fetched_at_epoch": time.time(), "tasks": tasks, "models": catalog}))
    return ranking, catalog, False


def _current_subset(models: dict, names) -> dict:
    profiles = models.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise SyncError("profiles is not a mapping")
    return {name: profiles.get(name) for name in names}


def recover(models_path: Path, state_dir: Path) -> None:
    pending_path = state_dir / "pending.json"
    if not pending_path.exists():
        return
    pending = read_json(pending_path)
    if pending.get("models_path") != str(models_path.absolute()):
        raise SyncError("Pending transaction belongs to another models.yml")
    current = decode_yaml(read_bytes(models_path).decode())
    subset = _current_subset(current, pending["after"])
    if subset == pending["after"]:
        atomic_write(state_dir / "state.json", json_bytes(pending["new_state"]))
    elif subset != pending["before"]:
        raise SyncError("Interrupted transaction conflicts with manual edits; no write performed")
    pending_path.unlink()


def mutate(models_path: Path, state_dir: Path, profile_id: str | None = None,
           profile: dict | None = None, rollback: bool = False, expected_sha: str | None = None,
           quality_guard=None) -> str:
    private_dir(state_dir)
    with gjc_lock(models_path):
        recover(models_path, state_dir)
        original = read_bytes(models_path)
        if expected_sha and hashlib.sha256(original).hexdigest() != expected_sha:
            raise SyncError("models.yml changed during planning; run sync again")
        text = original.decode("utf-8")
        models = decode_yaml(text)
        state_path = state_dir / "state.json"
        state = read_json(state_path) if state_path.exists() else {"managed": {}, "history": []}
        bound = state.get("models_path")
        if bound and bound != str(models_path.absolute()):
            raise SyncError("State directory belongs to another models.yml")
        if _current_subset(models, state["managed"]) != state["managed"]:
            raise SyncError("A managed profile was edited outside this tool; refusing to overwrite")
        if rollback:
            if not state["history"]:
                raise SyncError("No managed transaction to roll back")
            history = state["history"][-1]
            changes = history["before"]
            next_managed = history["previous_managed"]
            next_history = state["history"][:-1]
        else:
            if not profile_id or profile is None:
                raise SyncError("Missing profile to apply")
            present = (models.get("profiles") or {}).get(profile_id)
            if profile_id in (models.get("profiles") or {}) and profile_id not in state["managed"]:
                raise SyncError("Profile name collision: tool will not claim an existing user preset")
            if present == profile:
                return "unchanged"
            changes = {profile_id: profile}
            before = _current_subset(models, changes)
            next_managed = {**state["managed"], **changes}
            next_history = (state["history"] + [{"before": before, "previous_managed": state["managed"]}])[-10:]
        before = _current_subset(models, changes)
        if any(value is not None for value in changes.values()):
            if quality_guard is None:
                raise SyncError("Applying or restoring profiles requires a fresh quality guard")
            quality_guard(models, changes)
        replacement = patch_profiles(text, changes).encode()
        next_state = {"models_path": str(models_path.absolute()), "managed": next_managed, "history": next_history}
        backups = state_dir / "backups"
        private_dir(backups)
        backup = backups / (str(time.time_ns()) + ".models.yml")
        atomic_write(backup, original)
        pending = {"models_path": str(models_path.absolute()), "before": before, "after": changes, "new_state": next_state}
        atomic_write(state_dir / "pending.json", json_bytes(pending))
        # Detect non-cooperating editors too; GJC's own writes use the held directory lock.
        if read_bytes(models_path) != original:
            raise SyncError("models.yml changed concurrently; transaction aborted")
        atomic_write(models_path, replacement)
        atomic_write(state_path, json_bytes(next_state))
        (state_dir / "pending.json").unlink()
        for old in sorted(backups.glob("*.models.yml"))[:-10]:
            if not old.is_symlink():
                old.unlink()
        return "rolled_back" if rollback else "applied"


def initial_policy(models: dict) -> dict:
    """Use only selectors already referenced by user profiles; no new provider is authorized."""
    selectors = []
    for name, profile in (models.get("profiles") or {}).items():
        if name.startswith("or-") or not isinstance(profile, dict):
            continue
        for value in (profile.get("model_mapping") or {}).values():
            for selector in value if isinstance(value, list) else [value]:
                try:
                    local_model(selector, models)
                except SyncError:
                    continue
                if selector not in selectors:
                    selectors.append(selector)
    if not selectors:
        raise SyncError("No registered model pins in existing profiles; supply an explicit policy")
    # These are local role policies, not claimed OpenRouter classifier outputs.
    role_tasks = {
        "default": {"agent:*": 1, "code:general_impl": 1},
        "executor": {"code:general_impl": 1},
        "planner": {"agent:*": 1},
        "architect": {"code:*": 1},
        "critic": {"code:debugging": 1},
    }
    return {"version": 2, "quality": {"status": "draft"}, "profile_id": "or-auto", "metric": "request_share", "cache_hours": 6,
            "max_age_hours": 72, "allowed_selectors": selectors, "aliases": {},
            "filters": {"require_tools": True, "min_context": 32768},
            "roles": {r: {"tasks": t, "top_k": 3} for r, t in role_tasks.items()}}
