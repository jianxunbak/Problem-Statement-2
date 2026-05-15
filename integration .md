# 🏗️ Bot-i: Revit → Notion BIM Assistant
> Technical Integration Summary for Agent A

---

## 1. System Overview

Bot-i is a cloud-based BIM data pipeline that extracts structured element data from Autodesk Revit models and synchronizes it to Notion databases via a natural language Claude AI interface.

### Core Capabilities

| Capability | Description |
|---|---|
| Universal Revit Extraction | Extracts ALL model element categories (Rooms, Doors, Walls, Floors, Parking, Windows, Furniture, etc.) via PyRevit button |
| Cloud Data Store | Uploads extracted data to a Railway-hosted FastAPI server, stored in-memory + JSON snapshots |
| Natural Language Query | Users interact via `/chat` web UI — Claude AI interprets requests and executes Notion operations |
| Category + Keyword + Level Filtering | Filter elements before pushing to Notion |
| Change Detection | Compares two snapshots and reports Added / Deleted / Modified elements by `unique_id` |
| Background Push | All Notion pushes run in background threads with job status tracking |
| Two Push Modes | New database (snapshot) or versioned push to fixed database |

---

## 2. Tech Stack

### Server (Railway Cloud)

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.13.13 |
| Web Framework | FastAPI | latest |
| ASGI Server | Uvicorn | latest |
| AI SDK | Anthropic Python SDK | latest |
| HTTP Client | Requests | latest |
| Data Validation | Pydantic | latest |
| Concurrency | Python `threading` (stdlib) | — |
| File I/O | Python `json`, `os` (stdlib) | — |

### PyRevit Button (Revit Client)

| Component | Technology | Version |
|---|---|---|
| Language | IronPython | 2.x |
| Revit API | Autodesk.Revit.DB | 2026 |
| Plugin Framework | PyRevit | latest |
| HTTP Client | `System.Net.WebClient` | .NET |
| JSON | IronPython `json` stdlib | — |

### Infrastructure

| Component | Technology |
|---|---|
| Cloud Hosting | Railway |
| Source Control | GitHub (auto-deploy on push) |
| Database | Notion API (v1) |
| AI Model | `claude-sonnet-4-20250514` |

---

## 3. Architecture & Project Structure

```
bot-i/
├── server.py                  # FastAPI app — main entry point
│                              # Contains: REST endpoints, Claude agent,
│                              # Notion helpers, background jobs, Chat UI
├── pyrevit_button/
│   └── script.py              # IronPython PyRevit button script
│                              # Runs inside Revit, extracts model data,
│                              # POSTs to server or pushes directly to Notion
├── requirements.txt           # Python dependencies for Railway
├── Procfile                   # Railway start command:
│                              # web: uvicorn server:app --host 0.0.0.0 --port $PORT
├── .env                       # Local only — NOT in GitHub
│                              # NOTION_TOKEN, ANTHROPIC_API_KEY
├── .env.example               # Template for other users
├── .gitignore                 # Excludes .env, __pycache__
├── README.md                  # Setup and usage documentation
└── /app/snapshots/            # Runtime only — created on Railway filesystem
    └── snapshot_YYYYMMDD_HHMMSS.json   # Auto-saved on each upload
```

### Key Entry Points

| File | Role |
|---|---|
| `server.py` | Single-file FastAPI app — all server logic lives here |
| `pyrevit_button/script.py` | Runs inside Revit via PyRevit — client-side only |

---

## 4. Data Flow & State Management

### Full Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  REVIT (Client)                                         │
│  script.py (IronPython)                                 │
│  FilteredElementCollector → all categories              │
│  clean_str() → safe_str() → JSON encode UTF-8           │
│  POST /upload-data → Railway server                     │
└────────────────────┬────────────────────────────────────┘
                     │ HTTP POST
                     ▼
┌─────────────────────────────────────────────────────────┐
│  RAILWAY SERVER (server.py)                             │
│                                                         │
│  /upload-data                                           │
│  ├── stores elements in _revit_data (in-memory list)    │
│  └── saves snapshot_YYYYMMDD_HHMMSS.json (filesystem)   │
│                                                         │
│  /chat  ←── Browser opens this URL                      │
│  /ask   ←── Browser POSTs user messages here            │
│  │                                                      │
│  │  Claude Agent Loop:                                  │
│  │  1. Receive user message                             │
│  │  2. Call claude-sonnet-4 with TOOLS                  │
│  │  3. Execute tool (get_revit_summary,                 │
│  │     preview_filtered_data, get_stats_by_level,       │
│  │     create_new_database, push_versioned,             │
│  │     compare_snapshots, push_change_report,           │
│  │     check_job_status)                                │
│  │  4. Return tool result to Claude                     │
│  │  5. Claude generates reply                           │
│  │  6. Return {reply, history} to browser               │
│  │                                                      │
│  └── Background threads → Notion API                    │
└────────────────────┬────────────────────────────────────┘
                     │ HTTPS REST
                     ▼
┌─────────────────────────────────────────────────────────┐
│  NOTION API                                             │
│  POST /v1/databases  → create database                  │
│  POST /v1/pages      → create records                   │
│  POST /v1/databases/{id}/query → read versions          │
└─────────────────────────────────────────────────────────┘
```

### State Management

| State | Storage | Lifetime |
|---|---|---|
| `_revit_data` | Python in-memory list | Until next upload or server restart |
| `_jobs` | Python in-memory dict | Until server restart |
| `_last_compare` | Python in-memory dict | Until next compare or restart |
| Snapshots | `/app/snapshots/*.json` | Persists across restarts, lost on redeploy |

---

## 5. APIs & Interfaces

### Base URL
```
https://problem-statement-2-production.up.railway.app
```
*(or `http://127.0.0.1:8000` for local dev)*

---

### REST Endpoints

#### `GET /`
Health check.

```json
{ "status": "Revit Automation Server running" }
```

---

#### `POST /upload-data`
Upload Revit elements from PyRevit button.

**Request:**
```json
{
  "elements": [
    {
      "unique_id":        "abc123-...",
      "element_id":       304521,
      "category":         "Door",
      "name":             "D2A_DOOR_SGL FD1",
      "number":           "D-001",
      "level":            "2A DECK",
      "comments":         "",
      "phase_created":    "New Construction",
      "phase_demolished": ""
    }
  ]
}
```

**Response:**
```json
{
  "status": "success",
  "summary": { "Door": 10873, "Wall": 9082, "Room": 7798 },
  "snapshot_note": "Previous snapshot available for comparison."
}
```

---

#### `GET /get-data`
Filter and retrieve elements.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `category` | string (optional) | Filter by category name |
| `keyword` | string (optional) | Substring match on `name` |
| `level` | string (optional) | Substring match on `level` |

**Response:**
```json
{
  "status": "success",
  "elements": [ { "unique_id": "...", "element_id": 304521, "..." : "..." } ],
  "count": 42
}
```

---

#### `GET /get-summary`
Returns element counts by category.

```json
{
  "summary": { "Door": 10873, "Wall": 9082, "Room": 7798 },
  "total": 28162
}
```

---

#### `POST /ask`
Main chat endpoint — accepts user message and conversation history, returns Claude reply.

**Request:**
```json
{
  "message": "How many doors on each level?",
  "history": []
}
```

**Response:**
```json
{
  "reply": "Here are the door counts by level:\n- 2A DECK: 42 doors\n- 3A DECK: 38 doors",
  "history": [
    { "role": "user", "content": "How many doors on each level?" },
    { "role": "assistant", "content": "..." }
  ]
}
```

> **Note:** `history` must be maintained by the caller and resent on every turn. The server is stateless between `/ask` calls.

---

#### `GET /job-status/{job_id}`
Check progress of a background Notion push job.

```json
{
  "status": "running",
  "message": "Pushing 50 / 366 records..."
}
```

Possible `status` values: `starting` / `running` / `done` / `error`

---

#### `GET /chat`
Returns the full HTML Chat UI page (browser-rendered). No parameters required.

---

### Claude Agent Tools (Internal to `/ask`)

| Tool | Trigger Phrase | Description |
|---|---|---|
| `get_revit_summary` | "what data is available" | Returns element count by category |
| `get_stats_by_level` | "how many X on each level" | Returns count by level (+ optional category filter) |
| `preview_filtered_data` | Before any push | Returns count + 5 sample names |
| `create_new_database` | "export to new database" | Background push → new Notion DB |
| `push_versioned` | "add as new version" | Background push → fixed Notion DB with version number |
| `compare_snapshots` | "what changed" | Diffs last 2 snapshots by `unique_id` |
| `push_change_report` | "push change report" | Pushes diff result to new Notion DB |
| `check_job_status` | "check status" | Returns job progress |

---

### Unified Element Schema

All categories share these base fields:

```json
{
  "unique_id":        "string (Revit UniqueId)",
  "element_id":       304521,
  "category":         "Door | Room | Wall | Floor | Parking | ...",
  "name":             "string (Family + Type for non-Room; Room Name for Rooms)",
  "number":           "string (Mark or Room Number)",
  "level":            "string",
  "comments":         "string",
  "phase_created":    "string",
  "phase_demolished": "string"
}
```

> `script.py` uses a **universal collector** (`FilteredElementCollector.WhereElementIsNotElementType()`) — it does not hardcode specific categories. Any non-annotation, non-system Revit category is included.

---

### Change Report Data Structure

Returned by `compare_snapshots` tool and stored in `_last_compare`:

```json
{
  "added": [
    { "category": "Door", "element_id": 304521, "name": "D2A_DOOR_SGL FD1", "level": "2A DECK" }
  ],
  "deleted": [
    { "category": "Room", "element_id": 182345, "name": "Meeting Room 01", "level": "Level 1" }
  ],
  "modified": [
    {
      "category": "Room",
      "element_id": 193021,
      "name": "Office 02",
      "level": "Level 1",
      "changes": [
        { "field": "area", "old_value": 45.2, "new_value": 48.6 }
      ]
    }
  ]
}
```

Fields compared for change detection: `name`, `level`, `area`, `number`, `width`, `height`, `length`, `volume`, `thickness`, `fire_rating`, `function`.

---

## 6. Known Constraints & Dependencies

### Required Environment Variables

| Variable | Where Set | Description |
|---|---|---|
| `NOTION_TOKEN` | Railway Variables | Notion Integration API token (`ntn_...`) |
| `ANTHROPIC_API_KEY` | Railway Variables | Anthropic API key (`sk-ant-...`) |

Both must be present for `/ask` and Notion push to function. Missing either causes a 500 error.

---

### Hardcoded Constants in `server.py`

| Constant | Description |
|---|---|
| `PARENT_PAGE_ID` | Notion parent page where new databases are created |
| `FIXED_DATABASE_ID` | Fixed Notion DB used for versioned pushes |
| `SNAPSHOT_DIR` | Railway filesystem path (`/app/snapshots`) |

---

### Constraints

| Constraint | Detail |
|---|---|
| Railway 60s timeout | Solved via background threading — `/ask` returns immediately with `job_id` |
| Snapshot persistence | JSON files survive restart but are lost on full redeploy |
| In-memory data | `_revit_data` lost on server restart — user must re-upload via PyRevit button |
| IronPython 2 | `script.py` must use .NET HTTP classes (`System.Net.WebClient`), no `requests` library |
| Notion rate limit | ~3 req/sec — large pushes (300+ records) take 2–3 minutes |
| Claude model | Hardcoded `claude-sonnet-4-20250514` in `/ask` endpoint |
| No authentication | Anyone with the Railway URL can access `/chat` and `/upload-data` |
| Max snapshots | Keeps only the latest 10 snapshots to save disk space |
| Single user data store | `_revit_data` is a single global list — concurrent uploads overwrite each other |

---

### External Dependencies

| Service | Purpose | Auth Method |
|---|---|---|
| Notion API | Database creation + record push | Bearer token (`NOTION_TOKEN`) |
| Anthropic API | Claude AI agent | API key (`ANTHROPIC_API_KEY`) |
| Railway | Cloud hosting + filesystem | GitHub auto-deploy |
| GitHub | Source control + CI/CD | Repo connection |
