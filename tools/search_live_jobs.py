"""Live job search tool — retrieves, matches, and returns real listings."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .job_sources.base import verify_application_url
from .job_sources.registry import (
    fetch_all_sources,
    initialize_default_adapters,
    load_live_jobs,
    merge_live_jobs,
    save_live_jobs,
)
from .match_jobs import calculate_match, load_json
from .memory import load_memory

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE_DIR / "data" / "profile.json"


def parse_posting_date(date_str: str | None) -> datetime | None:
    """Parse an ISO 8601 posting date safely with timezone awareness."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def calculate_age_hours(dt: datetime, reference_dt: datetime | None = None) -> float:
    """Calculate the age of a posting in hours relative to reference time."""
    now = reference_dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def search_live_jobs(
    sources: list[str] | None = None,
    search_query: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
    fresher_only: bool = False,
    min_match_score: float | None = None,
    max_age_hours: int | None = None,
    verify_urls: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Search live job sources with filtering and personalized matching.

    Args:
        sources: Which sources to query (default: all registered).
        search_query: Keyword to search for (passed to adapters).
        location: Filter results by location substring.
        employment_type: Filter by employment type (e.g. "Internship").
        fresher_only: If True, exclude jobs with confirmed experience requirements.
        min_match_score: Minimum match score to include (defaults to memory/profile: 75).
        max_age_hours: Maximum posting age in hours (e.g., 24, 72, 168).
        verify_urls: If True, verify application URL reachability.
        limit: Maximum results to return.

    Returns:
        Dict with "jobs", "confirmed_matches", "potential_matches", "stats", "errors", and "source_info".
    """
    # Initialize adapters if needed.
    initialize_default_adapters()

    # Build kwargs for adapters.
    fetch_kwargs: dict[str, Any] = {}
    if search_query:
        fetch_kwargs["search"] = search_query

    # Fetch from live sources.
    fetch_result = fetch_all_sources(sources=sources, **fetch_kwargs)

    live_jobs = fetch_result["jobs"]
    errors = fetch_result["errors"]
    stats = fetch_result["stats"]

    # Merge with persisted jobs (don't lose previous data on a failed fetch).
    existing_jobs = load_live_jobs()
    merged = merge_live_jobs(existing_jobs, live_jobs)
    save_live_jobs(merged)

    # Load profile for matching.
    try:
        profile = load_json(PROFILE_PATH)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Could not load profile: %s", exc)
        profile = {}

    # Merge dynamic preferences from memory into profile.
    try:
        memory = load_memory()
    except Exception:
        memory = {}

    if min_match_score is None:
        min_match_score = memory.get("minimum_match_score")
        if min_match_score is None:
            min_match_score = profile.get("minimum_match_score", 75)

    mem_roles = memory.get("preferred_roles", [])
    if mem_roles:
        existing_roles = profile.get("target_roles", [])
        profile["target_roles"] = list(mem_roles) + [
            r for r in existing_roles if r.lower() not in [m.lower() for m in mem_roles]
        ]

    mem_locs = memory.get("preferred_locations", [])
    if mem_locs:
        existing_locs = profile.get("preferred_locations", [])
        profile["preferred_locations"] = list(mem_locs) + [
            l for l in existing_locs if l.lower() not in [m.lower() for m in mem_locs]
        ]

    is_profile_fresher = (profile.get("experience_level") or "").lower().strip() == "fresher"

    # Score and filter all fetched jobs.
    matched_jobs: list[dict[str, Any]] = []

    for job in live_jobs:
        # Check freshness first if max_age_hours is specified.
        posted_dt = parse_posting_date(job.get("posted_at"))
        posting_age_hours = round(calculate_age_hours(posted_dt), 1) if posted_dt else None

        if max_age_hours is not None:
            if posted_dt is None or posting_age_hours is None or posting_age_hours > max_age_hours:
                continue

        # Adapt live job format for the matching engine.
        adapted = _adapt_for_matching(job)
        match_result = calculate_match(adapted, profile)

        # Apply location filter if specified.
        if location:
            job_loc = (job.get("location") or "").lower()
            if location.lower() not in job_loc:
                continue

        # Apply employment type filter if specified.
        if employment_type:
            job_type = (job.get("employment_type") or "").lower()
            if employment_type.lower() not in job_type:
                continue

        # Exclude confirmed-ineligible jobs for fresher recommendations.
        eligibility = match_result.get("eligibility_status") or job.get("fresher_eligibility", "unknown")
        if (fresher_only or is_profile_fresher) and (eligibility == "ineligible" or job.get("fresher_eligibility") == "confirmed_experience"):
            continue

        # Apply score filter.
        if match_result["match_score"] < min_match_score:
            continue

        # URL verification if requested.
        if verify_urls:
            url_check = verify_application_url(job.get("apply_url", ""))
            url_status = url_check["url_status"]
            app_status = url_check["application_status"]
            url_checked_at = url_check["checked_at"]
        else:
            url_status = job.get("url_status", "url_unchecked")
            app_status = job.get("application_status", "application_status_unknown")
            url_checked_at = None

        # Determine freshness status and tier.
        if posting_age_hours is not None:
            if max_age_hours is not None:
                freshness_status = "within_window" if posting_age_hours <= max_age_hours else "outside_window"
            else:
                freshness_status = "known_age"

            if posting_age_hours <= 24.0:
                freshness_tier = "last_24h"
            elif posting_age_hours <= 72.0:
                freshness_tier = "last_72h"
            elif posting_age_hours <= 168.0:
                freshness_tier = "last_7d"
            else:
                freshness_tier = "older"
        else:
            freshness_status = "unknown"
            freshness_tier = "unverified"

        is_confirmed = match_result.get("is_confirmed_match", False)
        is_potential = match_result.get("is_potential_match", False)
        match_category = (
            "confirmed_match"
            if is_confirmed
            else ("potential_match" if is_potential else "other")
        )

        # Build the result entry.
        entry = {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "employment_type": job.get("employment_type", ""),
            "posted_at": job.get("posted_at"),
            "posting_age_hours": posting_age_hours,
            "freshness_status": freshness_status,
            "freshness_tier": freshness_tier,
            "eligibility_status": match_result.get("eligibility_status", "unknown"),
            "fresher_eligibility": match_result.get("fresher_eligibility", job.get("fresher_eligibility", "unknown")),
            "eligibility_reason": match_result.get("eligibility_reason", ""),
            "match_score": match_result["match_score"],
            "score_breakdown": match_result["score_breakdown"],
            "matched_skills": match_result["matched_skills"],
            "missing_skills": match_result["missing_skills"],
            "meets_threshold": match_result["meets_threshold"],
            "eligible": match_result["eligible"],
            "is_confirmed_match": is_confirmed,
            "is_potential_match": is_potential,
            "match_category": match_category,
            "source": job.get("source", ""),
            "apply_url": job.get("apply_url", ""),
            "source_url": job.get("source_url", ""),
            "url_status": url_status,
            "application_status": app_status,
            "url_checked_at": url_checked_at,
            "listing_status": job.get("listing_status", "unknown"),
            "is_live_listing": True,
        }
        matched_jobs.append(entry)

    # Sort by match score descending.
    matched_jobs.sort(key=lambda j: j["match_score"], reverse=True)

    # Apply limit.
    matched_jobs = matched_jobs[:limit]

    confirmed_matches = [j for j in matched_jobs if j.get("is_confirmed_match")]
    potential_matches = [j for j in matched_jobs if j.get("is_potential_match")]

    stats["confirmed_matches_count"] = len(confirmed_matches)
    stats["potential_matches_count"] = len(potential_matches)

    return {
        "jobs": matched_jobs,
        "confirmed_matches": confirmed_matches,
        "potential_matches": potential_matches,
        "count": len(matched_jobs),
        "stats": stats,
        "errors": errors[:10],
        "source_info": (
            "Live listings from verified job APIs. "
            "Confirmed matches meet all skills, role, and location thresholds with verified fresher eligibility. "
            "Potential matches meet match thresholds with unknown fresher eligibility. "
            "Confirmed ineligible vacancies are excluded from fresher recommendations."
        ),
    }


def _adapt_for_matching(job: dict[str, Any]) -> dict[str, Any]:
    """Adapt a normalized live job to the format expected by calculate_match.

    The matching engine uses the schema fields:
    - is_fresher_friendly (bool or None)
    - experience_years (int or None)
    - fresher_eligibility (str)
    """
    eligibility = job.get("fresher_eligibility", "unknown")
    experience = job.get("experience_years")

    if eligibility == "confirmed_fresher":
        is_fresher_friendly = True
        exp_years = experience if experience is not None else 0
    elif eligibility == "confirmed_experience":
        is_fresher_friendly = False
        exp_years = experience if experience is not None else 1
    else:
        # Unknown: do not falsely claim fresher-friendly.
        is_fresher_friendly = None
        exp_years = None

    return {
        "id": job.get("id", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "skills": job.get("skills", []),
        "experience_years": exp_years,
        "employment_type": job.get("employment_type", ""),
        "is_fresher_friendly": is_fresher_friendly,
        "fresher_eligibility": eligibility,
        "apply_url": job.get("apply_url", ""),
    }
