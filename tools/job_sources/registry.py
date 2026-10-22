"""Job source registry — manages adapters, deduplication, and persistence."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import now_iso

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LIVE_JOBS_PATH = BASE_DIR / "data" / "live_jobs.json"
FETCH_STATUS_PATH = BASE_DIR / "data" / "fetch_status.json"


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, Any] = {}


def register_adapter(name: str, adapter: Any) -> None:
    """Register a job source adapter by name."""
    _ADAPTERS[name] = adapter


def get_adapter(name: str) -> Any | None:
    """Get a registered adapter by name."""
    return _ADAPTERS.get(name)


def list_adapters() -> list[str]:
    """Return names of all registered adapters."""
    return list(_ADAPTERS.keys())


def initialize_default_adapters() -> None:
    """Register the default set of adapters.

    Adapters that require configuration (board tokens, company slugs)
    are registered but will return helpful errors if unconfigured.
    """
    from .remotive import RemotiveAdapter
    from .greenhouse import GreenhouseAdapter
    from .lever import LeverAdapter

    # Remotive: works out of the box (no config needed).
    register_adapter("remotive", RemotiveAdapter())

    # Greenhouse: needs board tokens from environment or config.
    greenhouse_tokens = _get_config_list("GREENHOUSE_BOARD_TOKENS")
    register_adapter("greenhouse", GreenhouseAdapter(greenhouse_tokens))

    # Lever: needs company slugs from environment or config.
    lever_slugs = _get_config_list("LEVER_COMPANY_SLUGS")
    register_adapter("lever", LeverAdapter(lever_slugs))


def _get_config_list(env_var: str) -> list[str]:
    """Read a comma-separated list from an environment variable."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


DEFAULT_CACHE_TTL_SECONDS = 120


def get_cache_ttl(override_ttl: int | None = None) -> int:
    """Return the active cache TTL in seconds."""
    if override_ttl is not None:
        return max(0, override_ttl)
    raw = os.getenv("JOB_SOURCE_CACHE_TTL", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_CACHE_TTL_SECONDS


def get_source_fetch_status(source_name: str) -> dict[str, Any] | None:
    """Read the status entry for a source safely from disk."""
    if not FETCH_STATUS_PATH.exists():
        return None
    try:
        with FETCH_STATUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            entry = data.get(source_name)
            if isinstance(entry, dict):
                return entry
    except Exception as exc:
        logger.warning("Could not read fetch status: %s", exc)
    return None


def is_source_cache_valid(
    source_name: str,
    ttl_seconds: int,
    reference_dt: datetime | None = None,
) -> bool:
    """Check if cached results for source are still within TTL and valid."""
    if ttl_seconds <= 0:
        return False
    entry = get_source_fetch_status(source_name)
    if not entry or not isinstance(entry, dict):
        return False
    # Only reuse cache if last fetch was marked successful.
    if not entry.get("success", False):
        return False
    last_fetch_str = entry.get("last_fetch")
    if not last_fetch_str or not isinstance(last_fetch_str, str):
        return False
    try:
        dt = datetime.fromisoformat(last_fetch_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = reference_dt or datetime.now(timezone.utc)
        age_seconds = (now - dt).total_seconds()
        return 0 <= age_seconds < ttl_seconds
    except Exception:
        return False


def get_cached_jobs_for_source(source_name: str) -> list[dict[str, Any]]:
    """Retrieve persisted jobs matching source_name."""
    persisted = load_live_jobs()
    return [job for job in persisted if job.get("source") == source_name]


# ---------------------------------------------------------------------------
# Multi-source fetch with deduplication and caching
# ---------------------------------------------------------------------------

def fetch_all_sources(
    sources: list[str] | None = None,
    cache_ttl_seconds: int | None = None,
    force_refresh: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Fetch jobs from specified sources (or all registered) with TTL caching.

    Returns:
        {
            "jobs": [...],
            "errors": [...],
            "stats": {
                "total_retrieved": int,
                "total_valid": int,
                "total_rejected": int,
                "total_duplicated": int,
                "total_returned": int,
                "per_source": { "source_name": {...} }
            }
        }
    """
    if not _ADAPTERS:
        initialize_default_adapters()

    target_sources = sources or list(_ADAPTERS.keys())
    ttl = get_cache_ttl(cache_ttl_seconds)

    all_jobs: list[dict[str, Any]] = []
    all_errors: list[str] = []
    per_source_stats: dict[str, dict[str, Any]] = {}
    total_retrieved = 0
    total_valid = 0
    total_rejected = 0

    for source_name in target_sources:
        adapter = _ADAPTERS.get(source_name)
        if adapter is None:
            all_errors.append(f"Unknown source: {source_name}")
            continue

        # Check cache if not forcing refresh and within TTL
        can_use_cache = not force_refresh and is_source_cache_valid(source_name, ttl)

        if can_use_cache:
            cached_source_jobs = get_cached_jobs_for_source(source_name)
            status_entry = get_source_fetch_status(source_name) or {}
            expected_valid = status_entry.get("valid", 0)

            # Safely verify cached jobs actually exist before using cache
            if expected_valid == 0 or len(cached_source_jobs) > 0:
                retrieved = status_entry.get("retrieved", len(cached_source_jobs))
                valid = len(cached_source_jobs)
                rejected = status_entry.get("rejected", 0)

                all_jobs.extend(cached_source_jobs)
                per_source_stats[source_name] = {
                    "retrieved": retrieved,
                    "valid": valid,
                    "rejected": rejected,
                    "cached": True,
                }
                total_retrieved += retrieved
                total_valid += valid
                total_rejected += rejected
                continue

        # Cache miss, expired, or forced refresh — fetch live
        try:
            result = adapter.fetch_jobs(**kwargs)
        except Exception as exc:
            all_errors.append(
                f"Source '{source_name}' failed unexpectedly: {exc}"
            )
            per_source_stats[source_name] = {
                "retrieved": 0, "valid": 0, "rejected": 0, "cached": False,
            }
            _update_fetch_status(source_name, 0, 0, [str(exc)])
            continue

        source_jobs = result.get("jobs", [])
        source_errors = result.get("errors", [])
        source_stats = result.get("stats", {})

        all_jobs.extend(source_jobs)
        all_errors.extend(source_errors)

        retrieved = source_stats.get("retrieved", 0)
        valid = source_stats.get("valid", 0)
        rejected = source_stats.get("rejected", 0)

        per_source_stats[source_name] = {
            "retrieved": retrieved,
            "valid": valid,
            "rejected": rejected,
            "cached": False,
        }
        total_retrieved += retrieved
        total_valid += valid
        total_rejected += rejected

        # Record fetch status.
        _update_fetch_status(source_name, retrieved, valid, source_errors)

    # Deduplicate.
    deduped, dup_count = deduplicate_jobs(all_jobs)

    return {
        "jobs": deduped,
        "errors": all_errors,
        "stats": {
            "total_retrieved": total_retrieved,
            "total_valid": total_valid,
            "total_rejected": total_rejected,
            "total_duplicated": dup_count,
            "total_returned": len(deduped),
            "per_source": per_source_stats,
        },
    }


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_jobs(
    jobs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Deduplicate jobs by their stable ID.

    Returns (unique_jobs, duplicate_count).
    """
    seen: dict[str, dict[str, Any]] = {}
    dup_count = 0

    for job in jobs:
        job_id = job.get("id", "")
        if not job_id:
            # No ID — cannot deduplicate, include as-is.
            seen[id(job)] = job  # Use object id as fallback key.
            continue

        if job_id in seen:
            dup_count += 1
            # Update last_seen_at on the existing record.
            seen[job_id]["last_seen_at"] = now_iso()
        else:
            seen[job_id] = job

    return list(seen.values()), dup_count


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_live_jobs(jobs: list[dict[str, Any]]) -> None:
    """Persist live job listings to disk with atomic write."""
    LIVE_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = LIVE_JOBS_PATH.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, LIVE_JOBS_PATH)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def load_live_jobs() -> list[dict[str, Any]]:
    """Load previously saved live job listings."""
    if not LIVE_JOBS_PATH.exists():
        return []
    try:
        with LIVE_JOBS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load live jobs: %s", exc)
    return []


def merge_live_jobs(
    existing: list[dict[str, Any]],
    new_jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge new jobs into existing, updating last_seen_at for matches."""
    by_id: dict[str, dict[str, Any]] = {}

    for job in existing:
        job_id = job.get("id", "")
        if job_id:
            by_id[job_id] = job

    for job in new_jobs:
        job_id = job.get("id", "")
        if job_id and job_id in by_id:
            # Update existing record.
            by_id[job_id]["last_seen_at"] = now_iso()
            # Update mutable fields from fresh data.
            for field in ("title", "location", "description", "skills",
                          "employment_type", "apply_url", "source_url",
                          "listing_status"):
                if job.get(field):
                    by_id[job_id][field] = job[field]
        elif job_id:
            by_id[job_id] = job
        else:
            # No ID; append without dedup.
            by_id[f"_noid_{id(job)}"] = job

    return list(by_id.values())


def _update_fetch_status(
    source: str,
    retrieved: int,
    valid: int,
    errors: list[str],
) -> None:
    """Record fetch status for a source."""
    status_data: dict[str, Any] = {}
    if FETCH_STATUS_PATH.exists():
        try:
            with FETCH_STATUS_PATH.open("r", encoding="utf-8") as f:
                status_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            status_data = {}

    status_data[source] = {
        "last_fetch": now_iso(),
        "retrieved": retrieved,
        "valid": valid,
        "success": (len(errors) == 0 or valid > 0) and not any("failed unexpectedly" in str(e).lower() for e in errors),
        "error_count": len(errors),
        "last_error": str(errors[-1]) if errors else None,
    }

    FETCH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = FETCH_STATUS_PATH.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, FETCH_STATUS_PATH)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
