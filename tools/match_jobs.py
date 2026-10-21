
import json
import re
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE_DIR / "data" / "profile.json"
JOBS_PATH = BASE_DIR / "data" / "jobs.json"

WEIGHTS = {
    "skills": 50,
    "role": 20,
    "location": 15,
    "eligibility": 15,
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("node.js", "nodejs")
    value = value.replace("next.js", "nextjs")
    value = value.replace("full-stack", "full stack")
    value = value.replace("springboot", "spring boot")
    return re.sub(r"\s+", " ", value)


def get_skill_matches(
    job_skills: list[str],
    profile_skills: list[str],
) -> tuple[list[str], list[str], float]:
    profile_set = {normalize(skill) for skill in profile_skills}

    matched = [
        skill for skill in job_skills
        if normalize(skill) in profile_set
    ]
    missing = [
        skill for skill in job_skills
        if normalize(skill) not in profile_set
    ]

    score = (
        len(matched) / len(job_skills) * 100
        if job_skills else 100.0
    )

    return matched, missing, score


def calculate_role_score(
    title: str,
    target_roles: list[str],
) -> float:
    title = normalize(title)
    roles = [normalize(role) for role in target_roles]

    if any(role in title or title in role for role in roles):
        return 100.0

    # Match common equivalent titles.
    groups = [
        {"backend developer", "backend engineer", "backend intern"},
        {"full stack developer", "full stack engineer"},
        {"frontend developer", "frontend engineer", "react developer"},
        {"software engineer", "software developer"},
        {"java developer", "java software engineer"},
    ]

    for group in groups:
        title_matches = any(term in title for term in group)
        profile_matches = any(
            any(term in role for term in group)
            for role in roles
        )
        if title_matches and profile_matches:
            return 100.0

    if "backend" in title and any("backend" in role for role in roles):
        return 100.0

    if "react" in title and any("frontend" in role for role in roles):
        return 100.0

    return 0.0


def calculate_location_score(
    job_location: str,
    profile: dict[str, Any],
) -> float:
    location = normalize(job_location)
    preferences = [
        normalize(item)
        for item in profile.get("preferred_locations", [])
    ]

    for preferred in preferences:
        preferred_city = preferred.split(",")[0].strip()

        if preferred in location or location in preferred:
            return 100.0

        if preferred_city and preferred_city in location:
            return 100.0

    if profile.get("willing_to_relocate", False):
        relocation_cities = {
            "mumbai", "pune", "bengaluru", "bangalore",
            "hyderabad", "chennai", "delhi", "noida",
            "gurugram", "gurgaon",
        }

        if any(city in location for city in relocation_cities):
            return 100.0

    return 0.0


def calculate_match(
    job: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    matched, missing, skill_score = get_skill_matches(
        job.get("skills", []),
        profile.get("skills", []),
    )

    role_score = calculate_role_score(
        job.get("title", ""),
        profile.get("target_roles", []),
    )

    location_score = calculate_location_score(
        job.get("location", ""),
        profile,
    )

    is_fresher = (
        normalize(profile.get("experience_level", "")) == "fresher"
    )

    # IMPORTANT: use the actual key in data/jobs.json.
    fresher_friendly = job.get("is_fresher_friendly", False)
    experience_years = job.get("experience_years")

    if is_fresher:
        eligible = (
            fresher_friendly is True
            and experience_years is not None
            and experience_years <= 0
        )
    else:
        eligible = True

    eligibility_score = 100.0 if eligible else 0.0

    breakdown = {
        "skills": round(skill_score, 2),
        "role": round(role_score, 2),
        "location": round(location_score, 2),
        "eligibility": round(eligibility_score, 2),
    }

    total_score = sum(
        breakdown[key] * weight / 100
        for key, weight in WEIGHTS.items()
    )

    preferred_types = {
        normalize(item)
        for item in profile.get("preferred_employment_types", [])
    }
    employment_type = job.get("employment_type", "")

    employment_type_match = (
        normalize(employment_type) in preferred_types
        if preferred_types else True
    )

    minimum_score = profile.get("minimum_match_score", 60)

    return {
        "job_id": job.get("id"),
        "title": job.get("title", "Unknown title"),
        "company": job.get("company", "Unknown company"),
        "location": job.get("location", "Not specified"),
        "employment_type": employment_type,
        "match_score": round(total_score, 2),
        "meets_threshold": total_score >= minimum_score and eligible,
        "eligible": eligible,
        "employment_type_match": employment_type_match,
        "matched_skills": matched,
        "missing_skills": missing,
        "score_breakdown": breakdown,
        # IMPORTANT: use the actual key in data/jobs.json.
        "apply_url": job.get("apply_url", ""),
    }


def match_jobs(
    profile_path: Path = PROFILE_PATH,
    jobs_path: Path = JOBS_PATH,
) -> list[dict[str, Any]]:
    profile = load_json(profile_path)
    jobs = load_json(jobs_path)

    if not isinstance(profile, dict):
        raise ValueError("Profile JSON must contain an object.")

    if not isinstance(jobs, list):
        raise ValueError("Jobs JSON must contain a list.")

    results = [calculate_match(job, profile) for job in jobs]

    results.sort(
        key=lambda item: (
            item["meets_threshold"],
            item["employment_type_match"],
            item["match_score"],
        ),
        reverse=True,
    )

    return results


def main() -> None:
    try:
        profile = load_json(PROFILE_PATH)
        results = match_jobs()
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Could not load matching data: {error}")
        return

    minimum_score = profile.get("minimum_match_score", 60)

    print("\n=== Personalized Job Matches ===")
    print(f"Minimum match score: {minimum_score}%")
    print("Note: These are fictional examples, not live vacancies.\n")

    for item in results:
        status = (
            "ELIGIBLE MATCH"
            if item["meets_threshold"]
            else "REVIEW / NOT ELIGIBLE"
        )

        print(
            f'{item["match_score"]:6.2f}% | '
            f'{item["title"]} — {item["company"]}'
        )
        print(f'  Status: {status}')
        print(f'  Location: {item["location"]}')
        print(f'  Employment type: {item["employment_type"]}')
        print(f'  Eligible: {item["eligible"]}')
        print(
            "  Employment preference match: "
            f'{item["employment_type_match"]}'
        )
        print(
            "  Matched skills: "
            + (", ".join(item["matched_skills"]) or "None")
        )
        print(
            "  Missing skills: "
            + (", ".join(item["missing_skills"]) or "None")
        )
        print(f'  Score breakdown: {item["score_breakdown"]}')
        print(f'  Apply URL: {item["apply_url"]}\n')


if __name__ == "__main__":
    main()
