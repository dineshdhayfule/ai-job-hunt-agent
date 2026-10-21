
import json
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from tools.search_jobs import search_jobs
from tools.memory import (
    get_memory,
    update_preferences,
    record_search,
)

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise SystemExit(
        "GEMINI_API_KEY is missing. Check your .env file."
    )

client = genai.Client(api_key=api_key)
MODEL = "gemini-2.5-flash"


# -------------------- TOOL DEFINITIONS --------------------

search_jobs_tool = types.FunctionDeclaration(
    name="search_jobs",
    description=(
        "Search the available job dataset by skills, location, "
        "fresher eligibility, and employment type."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "skills": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Skills required by the job.",
            },
            "location": {
                "type": "STRING",
                "description": "Location such as Remote or Mumbai.",
            },
            "fresher_friendly_only": {
                "type": "BOOLEAN",
                "description": "Return only fresher-friendly jobs.",
            },
            "employment_type": {
                "type": "STRING",
                "description": "Internship or Full-time.",
            },
        },
    },
)

get_memory_tool = types.FunctionDeclaration(
    name="get_memory",
    description=(
        "Read saved user preferences and previous job searches. "
        "Use this when the user asks what you remember or when "
        "saved preferences are relevant to their request."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {},
    },
)

update_preferences_tool = types.FunctionDeclaration(
    name="update_preferences",
    description=(
        "Save changes to the user's job-search preferences. "
        "Only update fields the user explicitly asks to change. "
        "Supported fields: preferred_roles, preferred_locations, "
        "minimum_match_score, skills_to_learn, excluded_companies."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "preferred_roles": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "preferred_locations": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "minimum_match_score": {
                "type": "NUMBER",
            },
            "skills_to_learn": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "excluded_companies": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
        },
    },
)

record_search_tool = types.FunctionDeclaration(
    name="record_search",
    description=(
        "Save a job-search query, its filters, and result count "
        "to persistent search history."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "The job-search request.",
            },
            "filters": {
                "type": "OBJECT",
                "properties": {
                    "skills": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "location": {"type": "STRING"},
                    "employment_type": {"type": "STRING"},
                },
            },
            "results_count": {
                "type": "INTEGER",
            },
        },
        "required": ["query"],
    },
)

agent_tools = [
    types.Tool(
        function_declarations=[
            search_jobs_tool,
            get_memory_tool,
            update_preferences_tool,
            record_search_tool,
        ]
    )
]


# -------------------- TOOL EXECUTION --------------------

def execute_tool(name: str, args: dict) -> dict:
    """Execute a supported tool and return a JSON-compatible result."""

    if name == "search_jobs":
        result = search_jobs(**args)

        # Automatically save searches in persistent history.
        if isinstance(result, list):
            count = len(result)
        elif isinstance(result, dict):
            count = next(
                (
                    len(result[key])
                    for key in ("jobs", "results")
                    if isinstance(result.get(key), list)
                ),
                result.get("count", 0),
            )
        else:
            count = 0

        record_search(
            query="Job search requested through the agent",
            filters=args,
            results_count=count if isinstance(count, int) else 0,
        )

        return {"result": result}

    if name == "get_memory":
        return {"memory": get_memory()}

    if name == "update_preferences":
        updated = update_preferences(args)
        return {
            "success": True,
            "message": "Preferences saved successfully.",
            "memory": updated,
        }

    if name == "record_search":
        entry = record_search(
            query=args["query"],
            filters=args.get("filters"),
            results_count=args.get("results_count", 0),
        )
        return {"success": True, "recorded_search": entry}

    return {"error": f"Unsupported tool: {name}"}


# -------------------- AGENT --------------------

def run_agent(user_request: str) -> None:
    """Run Gemini, execute its tool calls, and return the final answer."""

    response = client.models.generate_content(
        model=MODEL,
        contents=user_request,
        config=types.GenerateContentConfig(
            tools=agent_tools,
            system_instruction=(
                "You are a personalized job-search assistant. "
                "Use tools to search jobs and manage persistent memory. "
                "When the user asks to remember or change a preference, "
                "call update_preferences instead of merely claiming to "
                "save it. When asked about saved information, call "
                "get_memory. Use record_search when the user explicitly "
                "asks to save a search. Never claim a change succeeded "
                "unless the tool succeeds. Never invent job listings. "
                "The current job dataset contains fictional examples, "
                "not real vacancies. Do not overwrite unrelated "
                "preferences when updating one field."
            ),
        ),
    )

    function_calls = response.function_calls

    if not function_calls:
        print("\n[Agent answer]")
        print(response.text or "Gemini returned no text.")
        return

    tool_responses = []

    for call in function_calls:
        if call.name not in {
            "search_jobs",
            "get_memory",
            "update_preferences",
            "record_search",
        }:
            continue

        args = dict(call.args or {})

        print(f"\n[Agent selected tool: {call.name}]")

        try:
            result = execute_tool(call.name, args)
        except (ValueError, OSError, json.JSONDecodeError) as error:
            result = {
                "success": False,
                "error": str(error),
            }
        except Exception as error:
            result = {
                "success": False,
                "error": f"Tool execution failed: {error}",
            }

        tool_responses.append(
            types.Part.from_function_response(
                name=call.name,
                response=result,
            )
        )

    if not tool_responses:
        print("The model requested no supported tool call.")
        return

    # Send the tool results back to Gemini for a user-facing response.
    final_response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_request)],
            ),
            response.candidates[0].content,
            types.Content(
                role="user",
                parts=tool_responses,
            ),
        ],
        config=types.GenerateContentConfig(
            system_instruction=(
                "Answer using the tool results. Be clear and concise. "
                "Only claim that preferences were saved if the tool "
                "result confirms success. For job results, include "
                "titles, companies, locations, and URLs when available. "
                "Clearly identify fictional sample listings. "
                "Do not expose API keys or secrets."
            ),
        ),
    )

    print("\n[Agent answer]")
    print(final_response.text or "Gemini returned no final text.")


if __name__ == "__main__":
    print("AI Job Hunt Agent")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            request = input("You: ").strip()

            if request.lower() in {"exit", "quit"}:
                print("Goodbye!")
                break

            if not request:
                continue

            run_agent(request)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as error:
            print(f"\nAgent error: {error}")
