
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_PATH = BASE_DIR / "data" / "memory.json"

DEFAULT_MEMORY = {
    "preferred_roles": [],
    "preferred_locations": [],
    "minimum_match_score": 60,
    "skills_to_learn": [],
    "excluded_companies": [],
    "search_history": [],
}

EDITABLE_FIELDS = {
    "preferred_roles",
    "preferred_locations",
    "minimum_match_score",
    "skills_to_learn",
    "excluded_companies",
}

LIST_FIELDS = {
    "preferred_roles",
    "preferred_locations",
    "skills_to_learn",
    "excluded_companies",
}


def load_memory() -> dict[str, Any]:
    """Load persistent memory, creating the file if needed."""
    if not MEMORY_PATH.exists():
        save_memory(DEFAULT_MEMORY.copy())

    with MEMORY_PATH.open("r", encoding="utf-8") as file:
        memory = json.load(file)

    if not isinstance(memory, dict):
        raise ValueError("Memory file must contain a JSON object.")

    # Add missing fields without overwriting existing preferences.
    for key, value in DEFAULT_MEMORY.items():
        if key not in memory:
            memory[key] = value.copy() if isinstance(value, list) else value

    return memory


def save_memory(memory: dict[str, Any]) -> None:
    """Save memory using a temporary file to reduce corruption risk."""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MEMORY_PATH.with_suffix(".tmp")

    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(memory, file, indent=2, ensure_ascii=False)
            file.write("\n")

        os.replace(temporary_path, MEMORY_PATH)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def get_memory() -> dict[str, Any]:
    """Return saved preferences and search history."""
    return load_memory()


def update_preferences(
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Validate and persist user preference changes."""
    if not isinstance(updates, dict) or not updates:
        raise ValueError("Provide a non-empty dictionary of updates.")

    unknown_fields = set(updates) - EDITABLE_FIELDS
    if unknown_fields:
        raise ValueError(
            f"Unsupported fields: {', '.join(sorted(unknown_fields))}"
        )

    memory = load_memory()

    for key, value in updates.items():
        if key in LIST_FIELDS:
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip()
                for item in value
            ):
                raise ValueError(
                    f"{key} must be a list of non-empty strings."
                )

            # Remove duplicates while preserving order.
            memory[key] = list(
                dict.fromkeys(item.strip() for item in value)
            )

        elif key == "minimum_match_score":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 100
            ):
                raise ValueError(
                    "minimum_match_score must be between 0 and 100."
                )

            # Gemini may interpret "75%" as 0.75.
            # Convert decimal percentages to whole percentages.
            if 0 < value < 1:
                value *= 100

            memory[key] = value

    save_memory(memory)
    return memory


def record_search(
    query: str,
    filters: dict[str, Any] | None = None,
    results_count: int = 0,
) -> dict[str, Any]:
    """Save a search to persistent history."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query cannot be empty.")

    if (
        isinstance(results_count, bool)
        or not isinstance(results_count, int)
        or results_count < 0
    ):
        raise ValueError("results_count must be a non-negative integer.")

    if filters is not None and not isinstance(filters, dict):
        raise ValueError("filters must be a dictionary.")

    memory = load_memory()

    entry = {
        "query": query.strip(),
        "filters": filters or {},
        "results_count": results_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    memory["search_history"].append(entry)

    # Keep only the most recent 50 searches.
    memory["search_history"] = memory["search_history"][-50:]

    save_memory(memory)
    return entry


if __name__ == "__main__":
    memory = get_memory()

    print("Memory module loaded successfully.")
    print(f"Saved preferred roles: {len(memory['preferred_roles'])}")
    print(
        "Saved preferred locations: "
        f"{len(memory['preferred_locations'])}"
    )
    print(f"Minimum match score: {memory['minimum_match_score']}")
    print(f"Previous searches: {len(memory['search_history'])}")
