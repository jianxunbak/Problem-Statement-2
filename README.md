# 🏗️ Revit → Notion BIM Assistant

A BIM data pipeline that extracts model data from Autodesk Revit via a PyRevit button and pushes it to Notion — either directly or through a conversational Claude AI agent.

---

## Overview

This tool was built for architectural and engineering teams who want to sync Revit model data to Notion for project tracking, reporting, and team collaboration — without manual data entry.

The pipeline supports two workflows:

**Direct Export (no AI)**
- Mode 1: Auto-create a new Notion database (snapshot)
- Mode 2: Push to a fixed database with version tracking

**AI-Assisted Export (via Claude Agent)**
- Upload all model data to a local/cloud server
- Chat with Claude in your terminal to filter and push specific data
- Example: *"Export only FD1 fire doors on DECK 1A into a new database"*

---

## Project Structure

```
bot-i/
├── pyrevit_button/
│   └── script.py        # PyRevit button script (runs inside Revit)
├── server.py            # FastAPI server (stores Revit data, handles Notion API)
├── agent.py             # Claude AI agent (terminal chat interface)
├── .env.example         # Environment variable template
├── requirements.txt     # Python dependencies
└── README.md
```

---

## Data Extracted from Revit

| Category | Fields |
|----------|--------|
| **Room** | Name, Number, Level, Area, Perimeter, Height, Department, Occupancy, Comments, Phase |
| **Door** | Family+Type Name, Mark, Level, Width, Height, Fire Rating, Frame Material, Phase |
| **Wall** | Family+Type Name, Mark, Level, Length, Area, Volume, Base/Top Constraint, Function, Phase |
| **Floor** | Family+Type Name, Mark, Level, Area, Volume, Thickness, Structural, Phase |
| **Parking** | Family+Type Name, Mark, Level, Comments, Phase |

All elements also include `UniqueId` and `Element ID` for traceability back to Revit.

---

## Setup

### Prerequisites

- Autodesk Revit 2024–2026 with [PyRevit](https://github.com/eirannejad/pyRevit) installed
- Python 3.9+
- A [Notion Integration Token](https://www.notion.so/my-integrations)
- An [Anthropic API Key](https://console.anthropic.com/) (for Claude Agent)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up environment variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
NOTION_TOKEN=your_notion_token_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

### 3. Configure `pyrevit_button/script.py`

Fill in your Notion credentials and page IDs at the top of the file:

```python
NOTION_TOKEN      = "your_notion_token_here"
PARENT_PAGE_ID    = "your_parent_page_id"      # for Mode 1
FIXED_DATABASE_ID = "your_database_id"         # for Mode 2
AUTOMATION_SERVER = "http://127.0.0.1:8000"    # or your Railway URL
```

### 4. Install PyRevit button

Copy the `pyrevit_button/` folder into your PyRevit extension tab directory and reload PyRevit in Revit.

---

## Usage

### Direct Export (Mode 1 / Mode 2)

1. Open your Revit model
2. Click the **Export** button in the PyRevit tab
3. Select a mode:
   - **Mode 1** — Creates a new Notion database named `Revit Export YYYY-MM-DD`
   - **Mode 2** — Pushes to your fixed database with an auto-incremented version number
   - **Upload to Server** — Stores data for Claude Agent use

### AI-Assisted Export (Claude Agent)

**Terminal 1** — Start the server:
```bash
uvicorn server:app --reload
```

**Revit** — Click the PyRevit button and select **Upload to Server**

**Terminal 2** — Start the agent:
```bash
# Set your API key first
export ANTHROPIC_API_KEY=sk-ant-...        # Mac/Linux
$env:ANTHROPIC_API_KEY="sk-ant-..."        # Windows PowerShell

python agent.py
```

Then chat with Claude:
```
You: export all FD1 doors into a new database called "Fire Doors"
You: give me parking on MSCP DECK 1A only
You: push all rooms as a new version to the existing database
```

---

## Cloud Deployment (Railway)

To make the server accessible to your whole team:

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variable in Railway: `NOTION_TOKEN=your_token`
4. Railway will give you a public URL, e.g. `https://bot-i.up.railway.app`
5. Update `AUTOMATION_SERVER` in `script.py` and `agent.py` to your Railway URL

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `NOTION_TOKEN` | Notion Integration Token from notion.so/my-integrations |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude agent |

---

## Tech Stack

- **PyRevit** — Revit API scripting (IronPython 2, Revit 2026)
- **FastAPI** — Lightweight REST server
- **Anthropic Claude** — AI agent with tool use
- **Notion API** — Database creation and record management
- **Railway** — Cloud deployment

---

## Notes

- The server stores data **in memory** — data is lost if the server restarts. Re-click the PyRevit button to re-upload after a server restart.
- Notion API rate limits mean large exports (1000+ records) may take several minutes.
- IronPython 2 inside Revit does not support `requests` — the PyRevit script uses `System.Net.WebClient` instead.
