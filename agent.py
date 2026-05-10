import os
import json
import requests
import anthropic
from datetime import date

AUTOMATION_SERVER = "http://127.0.0.1:8000"
MODEL_NAME        = "claude-sonnet-4-20250514"
PARENT_PAGE_ID    = "3492bc72289b8081970ac57e2816e0c5"
FIXED_DATABASE_ID = "3492bc72289b80dda791c15cf5a575e4"

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ----------------------------------------------------------------
# Tools
# ----------------------------------------------------------------
tools = [
    {
        "name": "get_revit_summary",
        "description": (
            "Get a count of all Revit elements by category. "
            "Call this first to tell the user what data is available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "preview_filtered_data",
        "description": (
            "Preview how many elements match a given category and/or keyword filter "
            "before pushing to Notion. Use this to confirm count with the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category: Room, Door, Wall, Floor, or Parking.",
                    "enum": ["Room", "Door", "Wall", "Floor", "Parking"]
                },
                "keyword": {
                    "type": "string",
                    "description": (
                        "Filter by keyword in element name (case-insensitive substring match). "
                        "E.g. '550x2090' returns only elements whose name contains '550x2090'."
                    )
                }
            },
            "required": []
        }
    },
    {
        "name": "create_new_database",
        "description": (
            "Create a new Notion database and push Revit elements into it. "
            "Optionally filter by category and/or keyword. "
            "Only call this after confirming the count with the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Name for the new Notion database."
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category: Room, Door, Wall, Floor, or Parking.",
                    "enum": ["Room", "Door", "Wall", "Floor", "Parking"]
                },
                "keyword": {
                    "type": "string",
                    "description": (
                        "Filter by keyword in element name (case-insensitive substring match). "
                        "E.g. '550x2090' pushes only elements whose name contains '550x2090'."
                    )
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "push_versioned",
        "description": (
            "Push Revit elements into the existing fixed Notion database "
            "with an auto-incremented version number. "
            "Optionally filter by category and/or keyword."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category: Room, Door, Wall, Floor, or Parking.",
                    "enum": ["Room", "Door", "Wall", "Floor", "Parking"]
                },
                "keyword": {
                    "type": "string",
                    "description": "Filter by keyword in element name (case-insensitive substring match)."
                }
            },
            "required": []
        }
    }
]


# ----------------------------------------------------------------
# Tool implementations
# ----------------------------------------------------------------
def get_revit_summary():
    res = requests.get(f"{AUTOMATION_SERVER}/get-summary")
    return res.json()


def preview_filtered_data(category=None, keyword=None):
    params = {}
    if category:
        params["category"] = category
    if keyword:
        params["keyword"] = keyword
    res  = requests.get(f"{AUTOMATION_SERVER}/get-data", params=params)
    data = res.json()
    # Return a lightweight preview, not the full element list
    elements = data.get("elements", [])
    sample_names = [e.get("name", "") for e in elements[:5]]
    return {
        "count":        len(elements),
        "category":     category or "all",
        "keyword":      keyword or None,
        "sample_names": sample_names
    }


def create_new_database(title, category=None, keyword=None):
    # 1. Fetch filtered elements from server
    params = {}
    if category:
        params["category"] = category
    if keyword:
        params["keyword"] = keyword
    res      = requests.get(f"{AUTOMATION_SERVER}/get-data", params=params)
    elements = res.json().get("elements", [])

    if not elements:
        return {"error": f"No elements found for category='{category}', keyword='{keyword}'"}

    # 2. Create Notion database
    create_res  = requests.post(f"{AUTOMATION_SERVER}/create-database", json={
        "parent_page_id": PARENT_PAGE_ID,
        "title": title
    })
    create_data = create_res.json()
    if create_data.get("status") != "success":
        return {"error": "Failed to create database", "detail": create_data}

    db_id = create_data["database_id"]

    # 3. Push elements
    push_res  = requests.post(f"{AUTOMATION_SERVER}/push-to-notion", json={
        "database_id": db_id,
        "elements":    elements,
        "export_date": str(date.today())
    })
    push_data = push_res.json()

    return {
        "status":   "success",
        "title":    title,
        "category": category or "all",
        "keyword":  keyword or None,
        "pushed":   push_data.get("pushed", 0),
        "failed":   push_data.get("failed", 0),
    }


def push_versioned(category=None, keyword=None):
    # 1. Fetch filtered elements
    params = {}
    if category:
        params["category"] = category
    if keyword:
        params["keyword"] = keyword
    elements = requests.get(f"{AUTOMATION_SERVER}/get-data", params=params).json().get("elements", [])

    if not elements:
        return {"error": f"No elements found for category='{category}', keyword='{keyword}'"}

    # 2. Get next version
    version = requests.get(
        f"{AUTOMATION_SERVER}/get-next-version",
        params={"database_id": FIXED_DATABASE_ID}
    ).json()["next_version"]

    # 3. Push
    push_data = requests.post(f"{AUTOMATION_SERVER}/push-to-notion", json={
        "database_id": FIXED_DATABASE_ID,
        "elements":    elements,
        "version":     version,
        "export_date": str(date.today())
    }).json()

    return {
        "status":   "success",
        "version":  version,
        "category": category or "all",
        "keyword":  keyword or None,
        "pushed":   push_data.get("pushed", 0),
        "failed":   push_data.get("failed", 0),
    }


tool_impls = {
    "get_revit_summary":    get_revit_summary,
    "preview_filtered_data": preview_filtered_data,
    "create_new_database":  create_new_database,
    "push_versioned":       push_versioned,
}


# ----------------------------------------------------------------
# System prompt
# ----------------------------------------------------------------
SYSTEM_PROMPT = """You are a helpful BIM assistant for an architectural firm.
You have access to Revit model data (Rooms, Doors, Walls, Floors, Parking) uploaded via PyRevit.

Your workflow when the user asks to export data to Notion:
1. Call get_revit_summary to see what's available and tell the user the counts.
2. If the user wants to filter by keyword or category, call preview_filtered_data first to confirm the count.
3. Ask: "Would you like to create a new Notion database, or add this as a new version to the existing database?"
4. Call create_new_database or push_versioned with title, category, and/or keyword.

Keyword filtering: if the user says something like "only doors with 550x2090", use keyword="550x2090" and category="Door".
Never call create_new_database or push_versioned more than once per user request.
If a tool call succeeds, report the result and stop.
Keep responses concise and friendly."""


# ----------------------------------------------------------------
# Claude API call
# ----------------------------------------------------------------
def call_claude(messages):
    return client.messages.create(
        model=MODEL_NAME,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
        tools=tools,
    )


# ----------------------------------------------------------------
# Main chat loop
# ----------------------------------------------------------------
def main():
    print("=" * 50)
    print("Revit -> Notion BIM Assistant")
    print("Type your request or 'quit' to exit")
    print("=" * 50)

    messages = []

    while True:
        user_input = input("\nYou: ").strip()
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("Goodbye!")
            break

        messages.append({"role": "user", "content": user_input})

        while True:
            response = call_claude(messages)
            messages.append({"role": "assistant", "content": response.content})

            tool_uses  = [b for b in response.content if b.type == "tool_use"]
            text_parts = [b.text for b in response.content if b.type == "text"]

            if text_parts:
                print(f"\nAssistant: {' '.join(text_parts)}")

            if not tool_uses:
                break

            tool_results = []
            for tool_block in tool_uses:
                print(f"\n[Using tool: {tool_block.name}]")
                try:
                    result = tool_impls[tool_block.name](**tool_block.input)
                    print(f"  -> {result}")
                except Exception as e:
                    result = {"error": str(e)}
                    print(f"  -> Error: {e}")

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tool_block.id,
                    "content":     json.dumps(result)
                })

            messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    main()