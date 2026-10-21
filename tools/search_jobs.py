
import json
from pathlib import Path
from typing import Any

# Resolve the dataset relative to this Python file.
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "jobs.json"


def search_jobs(
    skills: list[str] | None = None,
    location: str | None = None,
    fresher_friendly_only: bool = True,
    employment_type: str | None = None,
) -> dict[str, Any]:
    """Search the local job dataset using skills and job preferences."""

    with DATA_FILE.open("r", encoding="utf-8") as file:
        jobs = json.load(file)

    requested_skills = {
        skill.strip().casefold()
        for skill in (skills or [])
        if skill.strip()
    }

    results = []

    for job in jobs:
        job_skills = {skill.casefold() for skill in job["skills"]}

        # Every requested skill must match.
        if requested_skills and not requested_skills.issubset(job_skills):
            continue

        if location and location.casefold() not in job["location"].casefold():
            continue

        if fresher_friendly_only and not job["is_fresher_friendly"]:
            continue

        if (
            employment_type
            and employment_type.casefold() != job["employment_type"].casefold()
        ):
            continue

        results.append(job)

    return {
        "count": len(results),
        "jobs": results,
    }

