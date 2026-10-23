"""Export evaluated live job search results to CSV format.

Reads genuine live jobs retrieved from supported sources, evaluates them
using the matching engine and user profile, and exports to agent_output.csv.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.match_jobs import calculate_match, load_json, PROFILE_PATH
from tools.job_sources.registry import load_live_jobs, get_source_fetch_status
from tools.search_live_jobs import _adapt_for_matching, calculate_age_hours, parse_posting_date

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_CSV_PATH = BASE_DIR / "agent_output.csv"


def generate_agent_output_csv(output_path: Path = OUTPUT_CSV_PATH) -> list[dict[str, Any]]:
    """Generate agent_output.csv from real evaluated listings."""
    profile = load_json(PROFILE_PATH)
    min_match_score = profile.get("minimum_match_score", 50.0)

    live_jobs = load_live_jobs()
    evaluated_rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for job in live_jobs:
        posted_dt = parse_posting_date(job.get("posted_at"))
        posting_age_hours = round(calculate_age_hours(posted_dt, now), 1) if posted_dt else None

        if posting_age_hours is not None:
            if posting_age_hours <= 24.0:
                freshness_tier = "last_24h"
            elif posting_age_hours <= 72.0:
                freshness_tier = "last_72h"
            elif posting_age_hours <= 168.0:
                freshness_tier = "last_7d"
            else:
                freshness_tier = "older"
        else:
            freshness_tier = "unverified"

        adapted = _adapt_for_matching(job)
        match_result = calculate_match(adapted, profile)

        match_score = match_result["match_score"]
        breakdown = match_result.get("score_breakdown", {})
        eligibility_status = match_result.get("eligibility_status", "unknown")
        eligible = match_result.get("eligible", False)
        meets_threshold = match_score >= min_match_score

        is_confirmed = meets_threshold and (eligible is True)
        is_potential = meets_threshold and (eligibility_status == "unknown")

        if is_confirmed:
            match_category = "confirmed_match"
            is_recommended = True
        elif is_potential:
            match_category = "potential_match"
            is_recommended = True
        elif eligibility_status == "ineligible":
            match_category = "ineligible"
            is_recommended = False
        else:
            match_category = "below_threshold"
            is_recommended = False

        row = {
            "job_id": job.get("id", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "employment_type": job.get("employment_type", ""),
            "posted_at": job.get("posted_at", ""),
            "posting_age_hours": posting_age_hours if posting_age_hours is not None else "",
            "freshness_tier": freshness_tier,
            "match_score": match_score,
            "skills_score": breakdown.get("skills", 0.0),
            "role_score": breakdown.get("role", 0.0),
            "location_score": breakdown.get("location", 0.0),
            "eligibility_status": eligibility_status,
            "eligible": eligible,
            "meets_threshold": meets_threshold,
            "match_category": match_category,
            "is_recommended": is_recommended,
            "source": job.get("source", ""),
            "apply_url": job.get("apply_url", ""),
            "url_status": job.get("url_status", "url_unchecked"),
            "application_status": job.get("application_status", "application_status_unknown"),
        }
        evaluated_rows.append(row)

    # Sort: recommended first, then by match_score descending
    evaluated_rows.sort(
        key=lambda r: (r["is_recommended"], r["match_score"]),
        reverse=True,
    )

    headers = [
        "job_id",
        "title",
        "company",
        "location",
        "employment_type",
        "posted_at",
        "posting_age_hours",
        "freshness_tier",
        "match_score",
        "skills_score",
        "role_score",
        "location_score",
        "eligibility_status",
        "eligible",
        "meets_threshold",
        "match_category",
        "is_recommended",
        "source",
        "apply_url",
        "url_status",
        "application_status",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(evaluated_rows)

    return evaluated_rows


if __name__ == "__main__":
    rows = generate_agent_output_csv()
    rec_count = sum(1 for r in rows if r["is_recommended"])
    print(f"Exported {len(rows)} listings to {OUTPUT_CSV_PATH.name} ({rec_count} recommended).")
