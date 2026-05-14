# Technical Summary: BIM Revit → Notion Agent
> Generated for integration into Master Project (Agent A)

---

## 1. System Overview

### Primary Function
This agent is a **BIM Data Pipeline with AI-assisted export**. Its core purpose is to extract structured architectural model data from Autodesk Revit, store it temporarily in a cloud-hosted REST server, and allow users to query and push selective subsets of that data into Notion databases via a natural-language chat interface powered by Claude AI.

### Core Capabilities
- One-click extraction of BIM elements from Revit via a PyRevit toolbar button
- Cloud-hosted REST API (FastAPI on Railway) that acts as a stateful in-memory data store
- Browser-based Claude AI chat interface (`/chat`) for natural-language data export commands
- Keyword and category-based filtering of Revit elements before Notion push
- Two direct-push modes (new database / versioned database) that bypass the AI layer entirely
- Background-threaded Notion push to avoid HTTP timeout on large datasets
- Job status tracking (`/status/{job_id}`) for async operations

---

## 2. Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| BIM Client | **Autodesk Revit 2025/2026** | Host application |
| Revit scripting | **PyRevit** (IronPython 2.7) | Button extension framework |
| Revit API | **Autodesk.Revit.DB** | Element extraction |
| Server language | **Python 3.13** | FastAPI server and agent |
| Web framework | **FastAPI** | REST API + HTML chat UI |
| ASGI server | **Uvicorn** | Local dev and Railway runtime |
| AI SDK | **anthropic** (Python) | Claude claude-sonnet-4-20250514 |
| Notion integration | **Notion REST API v1** (`api.notion.com/v1`) | Direct HTTP via `requests` |
| HTTP client | **requests** | Used in server and agent |
| Data validation | **pydantic** | FastAPI request models |
| Deployment | **Railway** | Cloud PaaS, auto-deploy from GitHub |
| Version control | **GitHub** | Source of truth for Railway CI/CD |
| Package manager | **pip** | Python dependencies |

### `requirements.txt`
```
fastapi
uvicorn
requests
pydantic
anthropic
```

---

## 3. Architecture & Project Structure

### Folder Tree
```
bot-i/                              ← Project root (also GitHub repo root)
│
├── pyrevit_button/
│   └── script.py                   ← PyRevit IronPython 2 script (runs inside Revit)
│
├── server.py                       ← FastAPI app (deployed to Railway)
├── agent.py                        ← Local terminal-based Claude chat (dev/testing only)
│
├── Procfile                        ← Railway startup: `web: uvicorn server:app --host 0.0.0.0 --port $PORT`
├── requirements.txt
├── .env                            ← Local secrets (NOT committed to GitHub)
├── .env.example                    ← Template for other users
├── .gitignore                      ← Excludes .env, __pycache__, *.pyc
└── README.md
```

### Key File Roles

| File | Runtime Environment | Role |
|---|---|---|
| `pyrevit_button/script.py` | IronPython 2 inside Revit | Extracts BIM data, POSTs to server, shows UI dialog |
| `server.py` | Python 3 on Railway (cloud) | In-memory data store, Notion push logic, Claude chat endpoint, HTML UI |
| `agent.py` | Python 3 on developer's local machine | Alternative terminal-based Claude interface for development testing |

---

## 4. Data Flow & State Management

### Full Pipeline

```
[Revit Model]
     │
     │  Revit API (IronPython 2)
     ▼
[script.py — PyRevit Button]
  - Collects: Rooms, Doors, Walls, Floors, Parking
  - Normalises to unified JSON element schema
  - User picks: Mode 1 | Mode 2 | Upload to Server
     │
     │  HTTP POST /upload-data  (System.Net.WebClient, IronPython-compatible)
     ▼
[server.py — FastAPI on Railway]
  - Stores elements in global in-memory list: _revit_data = []
  - State is ephemeral: lost on server restart
     │
     ├──── Mode 1/2: direct Notion push (no Claude)
     │         POST /create-database → Notion API
     │         POST /push-to-notion  → Notion API
     │
     └──── /chat or /ask: Claude-mediated push
               │
               │  HTTP POST /ask  { "message": "...", "history": [...] }
               ▼
         [Claude claude-sonnet-4-20250514]
           Tool: preview_filtered_data  → GET /get-data?category=X&keyword=Y
           Tool: create_new_database    → Notion API (background thread)
           Tool: push_versioned         → Notion API (background thread)
           Tool: check_job_status       → GET /status/{job_id}
               │
               ▼
         [Notion Database]
           - New DB per request (Mode 1 / create_new_database)
           - OR versioned rows in fixed DB (Mode 2 / push_versioned)
```

### State Management
- **In-memory only**: `_revit_data` is a Python list living in the FastAPI process. No database, no file persistence.
- **Job tracking**: Background push jobs are stored in a dict `_jobs = {}` keyed by UUID job_id. Each entry has `status` (`running` / `done` / `error`), `message`, and `pushed` count.
- **Stateless between server restarts**: If Railway restarts the dyno, all data is lost. User must re-click the PyRevit button.
- **Conversation history**: The `/ask` endpoint accepts a `history` array (list of `{role, content}` messages) which the caller must maintain and resend on each turn. Server itself is stateless between `/ask` calls.

---

## 5. APIs & Interfaces

### Base URL
```
https://problem-statement-2-production.up.railway.app
```
*(or `http://127.0.0.1:8000` for local dev)*

---

### Endpoint Reference

#### `GET /`
Health check.

**Response:**
```json
{ "status": "Revit Automation Server running" }
```

---

#### `POST /upload-data`
Called by PyRevit `script.py` after element extraction. Replaces the entire in-memory dataset.

**Request body:**
```json
{
  "elements": [
    {
      "name": "D2A_DOOR_DBL-STAIR FD1",
      "category": "Door",
      "number": "D-001",
      "unique_id": "a1b2c3d4-...",
      "element_id": 123456,
      "level": "MSCP DECK 1A",
      "width": 1.2,
      "height": 2.1,
      "fire_rating": "FD60",
      "frame_material": "Steel",
      "phase_created": "New Construction",
      "phase_demolished": "",
      "comments": ""
    }
  ]
}
```

**Response:**
```json
{
  "status": "success",
  "summary": { "Door": 93, "Room": 84, "Wall": 210, "Floor": 45, "Parking": 366 }
}
```

---

#### `GET /get-data`
Retrieve stored elements, optionally filtered.

**Query params:**
| Param | Type | Description |
|---|---|---|
| `category` | string (optional) | `Room`, `Door`, `Wall`, `Floor`, `Parking` |
| `keyword` | string (optional) | Case-insensitive substring match on `name` field |

**Response:**
```json
{
  "status": "success",
  "elements": [ { ...element schema... } ],
  "count": 42
}
```

---

#### `POST /create-database`
Create a new Notion database under a parent page.

**Request body:**
```json
{
  "parent_page_id": "3492bc72289b8081970ac57e2816e0c5",
  "title": "Doors Export 2025-05-14"
}
```

**Response:**
```json
{
  "status": "success",
  "database_id": "35a2bc72-289b-8187-8f0a-da6ebb5ddd5c"
}
```

---

#### `POST /push-to-notion`
Push a list of elements into an existing Notion database. Runs synchronously (use background thread wrapper for large sets).

**Request body:**
```json
{
  "database_id": "35a2bc72-289b-8187-8f0a-da6ebb5ddd5c",
  "elements": [ { ...element schema... } ],
  "version": 2,
  "export_date": "2025-05-14"
}
```

**Response:**
```json
{
  "status": "completed",
  "pushed": 93,
  "results": [
    { "name": "D2A_DOOR_DBL-STAIR FD1", "status": 200 },
    { "name": "D2A_DOOR_SGL FD1", "status": 200 }
  ]
}
```

---

#### `GET /get-next-version`
Query the highest existing version number in a Notion database and return the next.

**Query params:** `database_id` (string)

**Response:**
```json
{ "next_version": 3 }
```

---

#### `POST /ask`
**Primary integration endpoint.** Send a user message and conversation history; server runs Claude with tools and returns AI response. Handles tool use internally.

**Request body:**
```json
{
  "message": "Export all FD1 doors into a new Notion database called Fire Doors",
  "history": [
    { "role": "user", "content": "hi" },
    { "role": "assistant", "content": "Hello! How can I help you export Revit data?" }
  ]
}
```

**Response (success):**
```json
{
  "reply": "I found 42 FD1 doors. Push job started (job_abc123). Ask 'check status' to see progress.",
  "history": [
    { "role": "user", "content": "Export all FD1 doors..." },
    { "role": "assistant", "content": "I found 42 FD1 doors..." }
  ]
}
```

**Response (error):**
```json
{
  "reply": "Error connecting to server. Please try again.",
  "history": []
}
```

---

#### `GET /status/{job_id}`
Check progress of a background Notion push job.

**Response (running):**
```json
{ "job_id": "abc123", "status": "running", "message": "Pushing 42 elements...", "pushed": 0 }
```

**Response (done):**
```json
{ "job_id": "abc123", "status": "done", "message": "Done! Pushed 42 records.", "pushed": 42 }
```

---

#### `GET /chat`
Returns a browser-rendered HTML chat UI. No params. Used by end users directly in browser.

---

### Claude Tools (used internally by `/ask`)

These are the tools Claude can invoke during a `/ask` session. Relevant for Agent A if it needs to replicate or extend the tool layer.

| Tool | Description | Key Inputs |
|---|---|---|
| `preview_filtered_data` | Preview count and sample names before committing | `category`, `keyword` |
| `create_new_database` | Create Notion DB and start background push | `title`, `category`, `keyword` |
| `push_versioned` | Push to fixed DB with auto-incremented version | `category`, `keyword` |
| `check_job_status` | Poll background job result | `job_id` |

---

### Unified Element Schema

All categories share these base fields:

```json
{
  "name": "string",
  "category": "Room | Door | Wall | Floor | Parking",
  "number": "string",
  "unique_id": "string (Revit UniqueId)",
  "element_id": 123456,
  "level": "string",
  "phase_created": "string",
  "phase_demolished": "string"
}
```

**Category-specific fields:**

| Category | Extra fields |
|---|---|
| Room | `area`, `perimeter`, `height`, `department`, `occupancy`, `comments` |
| Door | `width`, `height`, `fire_rating`, `frame_material`, `comments` |
| Wall | `length`, `area`, `volume`, `base_constraint`, `top_constraint`, `unconnected_height`, `function` |
| Floor | `area`, `volume`, `thickness`, `structural`, `comments` |
| Parking | `mark` |

---

## 6. Known Constraints & Dependencies

### Required Environment Variables

| Variable | Where set | Description |
|---|---|---|
| `NOTION_TOKEN` | Railway Variables / `.env` | Notion Integration Token (`ntn_...`) |
| `ANTHROPIC_API_KEY` | Railway Variables / `.env` | Anthropic API key (`sk-ant-...`) |

Both must be present for `/ask` and Notion push to function. Missing either causes a 500 error.

### Hardcoded Configuration (must be updated per deployment)

| Constant | Location | Current value |
|---|---|---|
| `PARENT_PAGE_ID` | `server.py`, `agent.py` | Notion parent page for new databases |
| `FIXED_DATABASE_ID` | `server.py`, `agent.py` | Notion DB used by Mode 2 / `push_versioned` |
| `AUTOMATION_SERVER` | `script.py`, `agent.py` | Railway URL or `http://127.0.0.1:8000` |

### Constraints

- **Ephemeral state**: In-memory only. Server restart = data loss. PyRevit button must be clicked again.
- **IronPython 2 on client**: `script.py` cannot use `requests`, `json` with `encoding=` arg, or any CPython-only library. Uses `System.Net.WebClient` for HTTP.
- **Revit 2025/2026**: Uses `ElementId.Value` (64-bit). Incompatible with `IntegerValue` used in Revit 2024 and below (backwards compatibility guard exists in `get_element_id()`).
- **Railway 60s request timeout**: Large Notion pushes (300+ records) must use background threading. The `/ask` endpoint returns a `job_id` immediately; caller polls `/status/{job_id}`.
- **Notion API rate limit**: ~3 requests/second. Each element = 1 API call. 366 parking lots ≈ 110 seconds to push.
- **No authentication on REST API**: All endpoints on Railway are publicly accessible. Anyone with the URL can call `/upload-data` or `/ask`.
- **Single-user data store**: `_revit_data` is a single global list. Concurrent users from different Revit models will overwrite each other's data.
- **Model**: Hardcoded to `claude-sonnet-4-20250514`. Must be updated if model is deprecated.
- **Notion schema**: The property names in `build_props()` in `server.py` must exactly match the column names in the target Notion database. Schema mismatch causes silent 400 errors from Notion.