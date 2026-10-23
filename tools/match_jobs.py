
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
}
COMBINED_MATCH_WEIGHT = sum(WEIGHTS.values())  # 85


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
        if job_skills else 0.0
    )

    return matched, missing, score


UNRELATED_ROLE_PATTERNS = [
    re.compile(r"\bsales\b", re.IGNORECASE),
    re.compile(r"\bmarketing\b", re.IGNORECASE),
    re.compile(r"\bcopywrit(er|ing)\b", re.IGNORECASE),
    re.compile(r"\bcontent\s+writ(er|ing)\b", re.IGNORECASE),
    re.compile(r"\bfreelance\s+writer\b", re.IGNORECASE),
    re.compile(r"\boffice\s+assistant\b", re.IGNORECASE),
    re.compile(r"\bvirtual\s+assistant\b", re.IGNORECASE),
    re.compile(r"\bcustomer\s+support\b", re.IGNORECASE),
    re.compile(r"\bcustomer\s+service\b", re.IGNORECASE),
    re.compile(r"\bcontent\s+(reviewer|moderator)\b", re.IGNORECASE),
    re.compile(r"\btelemarket(er|ing)\b", re.IGNORECASE),
    re.compile(r"\baccountant\b", re.IGNORECASE),
    re.compile(r"\bbookkeeper\b", re.IGNORECASE),
    re.compile(r"\bhelp\s*desk\b", re.IGNORECASE),
    re.compile(r"\bservice\s*desk\b", re.IGNORECASE),
]

SENIOR_ROLE_PATTERNS = [
    re.compile(r"\bsenior\b", re.IGNORECASE),
    re.compile(r"\bsr\.?\b", re.IGNORECASE),
    re.compile(r"\blead\b", re.IGNORECASE),
    re.compile(r"\bstaff\b", re.IGNORECASE),
    re.compile(r"\bprincipal\b", re.IGNORECASE),
    re.compile(r"\bdirector\b", re.IGNORECASE),
    re.compile(r"\bhead\s+of\b", re.IGNORECASE),
    re.compile(r"\barchitect\b", re.IGNORECASE),
    re.compile(r"\bvp\b", re.IGNORECASE),
    re.compile(r"\bvice\s+president\b", re.IGNORECASE),
    re.compile(r"\bmanager\b", re.IGNORECASE),
]

RESTRICTED_REGION_PATTERNS = [
    re.compile(r"\b(usa?|united\s+states|u\.s\.a?|canada|uk|united\s+kingdom|europe|emea|latam|apac|australia|germany|france|netherlands)\s*(only|residents?|citizens?|based)\b", re.IGNORECASE),
    re.compile(r"\b(only|residents?|citizens?)\s+(in|from)\s+(the\s+)?(usa?|united\s+states|u\.s\.a?|canada|uk|europe|germany)\b", re.IGNORECASE),
    re.compile(r"\b(us|usa|canada|uk|europe)\s+only\b", re.IGNORECASE),
    re.compile(r"\b(us\s+residents?\s+only|usa\s+only|us\s+only)\b", re.IGNORECASE),
    re.compile(r"\b(north\s+america|us\s*/\s*canada|us\s+timezones?)\b", re.IGNORECASE),
    re.compile(r"\bremote\s*[-–,(/]\s*(us|usa|united states|uk|canada|europe|germany|france|australia|latam|emea)\b", re.IGNORECASE),
]

UNRESTRICTED_REMOTE_PATTERNS = [
    re.compile(r"\b(worldwide|anywhere|any\s+location|global)\b", re.IGNORECASE),
    re.compile(r"\bwork\s+from\s+anywhere\b", re.IGNORECASE),
]


def calculate_role_score(
    title: str,
    target_roles: list[str],
    experience_level: str | None = None,
) -> float:
    normalized_title = normalize(title)

    # Reject clearly unrelated non-technical job families.
    for pattern in UNRELATED_ROLE_PATTERNS:
        if pattern.search(normalized_title):
            return 0.0

    # If the user is a fresher/entry-level, reject senior/lead positions.
    if experience_level and normalize(experience_level) == "fresher":
        for pattern in SENIOR_ROLE_PATTERNS:
            if pattern.search(normalized_title):
                return 0.0

    roles = [normalize(role) for role in target_roles]

    if any(role in normalized_title for role in roles):
        return 100.0

    # Only match title in role if title is adequately descriptive
    if any(normalized_title in role and len(normalized_title) >= 10 for role in roles):
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
        title_matches = any(term in normalized_title for term in group)
        profile_matches = any(
            any(term in role for term in group)
            for role in roles
        )
        if title_matches and profile_matches:
            return 100.0

    if "backend" in normalized_title and any("backend" in role for role in roles):
        return 100.0

    if "react" in normalized_title and any("frontend" in role for role in roles):
        return 100.0

    return 0.0


def calculate_location_score(
    job_location: str,
    profile: dict[str, Any],
) -> float:
    location = normalize(job_location)
    if not location:
        return 100.0

    preferences = [
        normalize(item)
        for item in profile.get("preferred_locations", [])
    ]

    has_india_in_location = bool(re.search(r"\bindia\b", location))

    # Reject if contradictory non-India restriction is present and India is not explicitly included.
    for pattern in RESTRICTED_REGION_PATTERNS:
        if pattern.search(location) and not has_india_in_location:
            return 0.0

    has_remote_pref = any("remote" in p for p in preferences)

    # Unrestricted worldwide/anywhere remote matching
    if has_remote_pref:
        for pattern in UNRESTRICTED_REMOTE_PATTERNS:
            if pattern.search(location):
                return 100.0

    for preferred in preferences:
        preferred_parts = [p.strip() for p in preferred.split(",")]
        preferred_city = preferred_parts[0] if preferred_parts else ""

        # Handle remote-specific preference (e.g., "remote, india")
        if "remote" in preferred:
            if "india" in preferred:
                # Requires India presence or explicit match
                if "remote" in location and has_india_in_location:
                    return 100.0
                if location == preferred or location == "remote, india":
                    return 100.0
            else:
                if "remote" in location:
                    return 100.0
            continue

        # Physical location matching (e.g. "mumbai, india")
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
        profile.get("experience_level"),
    )

    location_score = calculate_location_score(
        job.get("location", ""),
        profile,
    )

    is_fresher = (
        normalize(profile.get("experience_level", "")) == "fresher"
    )

    # Check both boolean flag and string eligibility if present
    fresher_friendly = job.get("is_fresher_friendly")
    raw_eligibility = job.get("fresher_eligibility")
    experience_years = job.get("experience_years")

    if is_fresher:
        if raw_eligibility == "confirmed_fresher" or fresher_friendly is True:
            if experience_years is not None and experience_years > 0:
                eligibility_status = "ineligible"
                eligibility_reason = (
                    f"Contradictory listing: marked fresher-friendly but requires {experience_years}y experience"
                )
                eligible = False
            else:
                eligibility_status = "confirmed"
                eligibility_reason = "Confirmed fresher/entry-level suitable"
                eligible = True
        elif raw_eligibility == "confirmed_experience" or fresher_friendly is False:
            eligibility_status = "ineligible"
            eligibility_reason = (
                f"Requires {experience_years}y experience"
                if experience_years is not None and experience_years > 0
                else "Not fresher-friendly"
            )
            eligible = False
        else:
            # Missing or unknown eligibility
            if experience_years is not None and experience_years > 0:
                eligibility_status = "ineligible"
                eligibility_reason = f"Requires {experience_years}y experience"
                eligible = False
            else:
                eligibility_status = "unknown"
                eligibility_reason = "Experience requirement not specified by source"
                eligible = False
    else:
        eligibility_status = "confirmed"
        eligibility_reason = "Profile is not fresher"
        eligible = True

    breakdown = {
        "skills": round(skill_score, 2),
        "role": round(role_score, 2),
        "location": round(location_score, 2),
    }

    # Match quality calculated independently of eligibility, normalized over 85
    raw_score = (
        (skill_score * WEIGHTS["skills"])
        + (role_score * WEIGHTS["role"])
        + (location_score * WEIGHTS["location"])
    )
    total_score = round(raw_score / COMBINED_MATCH_WEIGHT, 2)

    preferred_types = {
        normalize(item)
        for item in profile.get("preferred_employment_types", [])
    }
    employment_type = job.get("employment_type", "")

    employment_type_match = (
        normalize(employment_type) in preferred_types
        if preferred_types else True
    )

    minimum_score = profile.get("minimum_match_score", 50)
    meets_threshold = total_score >= minimum_score

    return {
        "job_id": job.get("id"),
        "title": job.get("title", "Unknown title"),
        "company": job.get("company", "Unknown company"),
        "location": job.get("location", "Not specified"),
        "employment_type": employment_type,
        "match_score": total_score,
        "meets_threshold": meets_threshold,
        "score_qualifies": meets_threshold,
        "eligible": eligible,
        "eligibility_status": eligibility_status,
        "fresher_eligibility": eligibility_status,
        "eligibility_reason": eligibility_reason,
        "is_confirmed_match": meets_threshold and (eligible is True),
        "is_potential_match": meets_threshold and (eligibility_status == "unknown"),
        "employment_type_match": employment_type_match,
        "matched_skills": matched,
        "missing_skills": missing,
        "score_breakdown": breakdown,
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
            item["is_confirmed_match"],
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

    minimum_score = profile.get("minimum_match_score", 50)

    print("\n=== Personalized Job Matches ===")
    print(f"Minimum match score: {minimum_score}%")
    print("Note: These are fictional examples, not live vacancies.\n")

    for item in results:
        if item["is_confirmed_match"]:
            status = "CONFIRMED FRESHER MATCH"
        elif item["is_potential_match"]:
            status = "POTENTIAL MATCH (UNKNOWN ELIGIBILITY)"
        else:
            status = "REVIEW / NOT ELIGIBLE"

        print(
            f'{item["match_score"]:6.2f}% | '
            f'{item["title"]} — {item["company"]}'
        )
        print(f'  Status: {status}')
        print(f'  Eligibility: {item["eligibility_status"]}')
        print(f'  Meets threshold (>= {minimum_score}%): {item["meets_threshold"]}')
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
