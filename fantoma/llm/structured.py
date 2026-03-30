"""Structured LLM output — JSON schema for action responses.

Defines the JSON schema sent to llama-server via response_format,
and parses the structured JSON response into action strings that
the existing action_parser.py can execute.

Falls back gracefully: if JSON parsing fails, returns None so the
caller can fall back to text-based parsing.
"""
import json
import logging

log = logging.getLogger("fantoma.structured")

# JSON schema for the LLM's action response.
# llama-server accepts this via response_format.type = "json_schema".
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["CLICK", "TYPE", "SELECT", "SCROLL",
                                 "NAVIGATE", "PRESS", "WAIT", "DONE",
                                 "SEARCH_PAGE", "FIND"],
                    },
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "url": {"type": "string"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "key": {"type": "string"},
                },
                "required": ["action"],
            },
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["actions"],
}

# Sequence terminators — stop processing after these
_TERMINATORS = {"NAVIGATE", "DONE"}
_MAX_ACTIONS = 5


def parse_structured(raw: str) -> list[str] | None:
    """Parse a structured JSON action response into action strings.

    Returns a list of action strings like ["CLICK [3]", "TYPE [0] \"hello\""],
    or None if the response is not valid structured JSON.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    actions_list = data.get("actions")
    if not actions_list or not isinstance(actions_list, list):
        return None

    result = []
    for entry in actions_list[:_MAX_ACTIONS]:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action", "").upper()
        if not action:
            continue

        if action == "CLICK":
            idx = entry.get("index", 0)
            result.append(f"CLICK [{idx}]")
        elif action == "TYPE":
            idx = entry.get("index", 0)
            text = entry.get("text", "")
            result.append(f'TYPE [{idx}] "{text}"')
        elif action == "SELECT":
            idx = entry.get("index", 0)
            text = entry.get("text", "")
            result.append(f'SELECT [{idx}] "{text}"')
        elif action == "SCROLL":
            direction = entry.get("direction", "down")
            result.append(f"SCROLL {direction}")
        elif action == "NAVIGATE":
            url = entry.get("url", "")
            result.append(f"NAVIGATE {url}")
        elif action == "PRESS":
            key = entry.get("key", "Enter")
            result.append(f"PRESS {key}")
        elif action == "SEARCH_PAGE":
            text = entry.get("text", "")
            result.append(f'SEARCH_PAGE "{text}"')
        elif action == "FIND":
            text = entry.get("text", "")
            result.append(f'FIND "{text}"')
        elif action in ("WAIT", "DONE"):
            result.append(action)
        else:
            continue

        if action in _TERMINATORS:
            break

    return result if result else None


def get_response_format() -> dict:
    """Return the response_format dict for the LLM API call.

    Compatible with llama-server and OpenAI-compatible endpoints
    that support response_format.type = "json_schema".
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "browser_actions",
            "strict": True,
            "schema": ACTION_SCHEMA,
        },
    }
