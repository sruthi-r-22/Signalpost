# Signalpost 📡

> **Autonomous Corporate Intelligence & Verification Agent for Norwegian Enterprises**

Signalpost is an agentic company research platform that accepts a Norwegian organization number, resolves official public registry ground-truth from **Brønnøysundregistrene (Enhetsregisteret)**, retrieves supplementary public web intelligence, extracts structured corporate facts using an isolated LLM service, rigorously cross-verifies claims to prevent hallucinations, tracks evidence citations with timestamps, and maintains an audit trail across re-research runs.

---

## 1. Project Overview

Norwegian companies are uniquely identified by a 9-digit organization number governed by the Modulo 11 checksum algorithm. Signalpost treats this organization number as the primary identity key.

Instead of guessing or blindly scraping websites:
1. **Authoritative Baseline**: It first resolves the company via Norway's open Enhetsregisteret API for legal name, status, NACE codes, address, employees, foundation date, and registered leadership (CEO and Board).
2. **Supplementary Public Discovery**: It discovers external sources and web updates through pluggable search providers (DuckDuckGo, Tavily, SerpAPI, or Mock).
3. **Evidence-Grounded Extraction**: An isolated LLM layer extracts operational insights strictly constrained to retrieved evidence.
4. **Identity Verification & Conflict Resolution**: A verification engine tests for identity mismatches, prioritizes official government records over third-party claims, flags discrepancies, and bounds confidence scores.
5. **Freshness & Audit History**: Subsequent re-research runs detect modified, added, or unchanged facts, preserving historic evidence and logging diffs.

---

## 2. Architecture

```text
signalpost/
├── app/
│   ├── main.py                     # FastAPI application & static server
│   ├── config.py                   # Pydantic Settings & environment config
│   ├── models/
│   │   ├── __init__.py
│   │   ├── company.py              # Identity, Profile, Run, History models
│   │   └── evidence.py             # Evidence citations & ConflictItem schemas
│   ├── services/
│   │   ├── __init__.py
│   │   ├── company_resolver.py     # Brønnøysundregistrene API & Modulo 11 check
│   │   ├── search_service.py       # Pluggable search (Mock, DDG, Tavily, SerpAPI)
│   │   ├── extractor.py            # Evidence synthesis & field consolidation
│   │   ├── verifier.py             # Cross-check, conflict resolution & confidence
│   │   ├── researcher.py           # Pipeline orchestrator & refresh manager
│   │   └── llm_service.py          # Isolated LLM (OpenAI, Gemini, Groq, Ollama, Mock)
│   ├── database/
│   │   ├── __init__.py
│   │   └── db.py                   # SQLite persistence & audit logging
│   └── api/
│       ├── __init__.py
│       └── routes.py               # REST API endpoints
│
├── frontend/
│   ├── index.html                  # Responsive modern web interface
│   ├── style.css                   # Dark theme stylesheet
│   └── app.js                      # Client logic & pipeline visualization
├── scripts/
│   ├── run_batch.py                # Standalone batch research runner
│   └── test_companies.txt           # Example input list
│
├── tests/
│   ├── test_validation.py          # Modulo 11 and format validation tests
│   ├── test_resolver.py            # Enhetsregisteret parser tests
│   ├── test_verifier.py            # Conflict resolution & confidence tests
│   ├── test_evidence_and_db.py     # Evidence deduplication & database tests
│   └── test_api.py                 # REST endpoint integration tests
│
├── .env.example                    # Template environment variables
├── requirements.txt                # Python dependencies
└── README.md                       # Comprehensive project documentation
```

---

## 3. Features

- **Strict Norwegian Org Number Validation**: Validates 9 digits, strips extraneous characters, and enforces the standard Modulo 11 weighted checksum (`[3, 2, 7, 6, 5, 4, 3, 2]`).
- **Free, Open Official Registry Resolution**: Directly queries Brønnøysundregistrene's Enhetsregisteret and Roller APIs without requiring external API keys.
- **Evidence-First Architecture**: Every fact is linked to an exact source URL, retrieval timestamp, confidence rating, source type (`official_registry`, `company_website`, `public_search`), and quote.
- **Zero Hallucination Guarantee**: If evidence is missing, fields remain explicitly `null` rather than manufactured.
- **Pluggable LLM Integration**: Isolated LLM provider layer supporting OpenAI (GPT-4o / GPT-4o-mini), Google Gemini, Groq, OpenRouter, local Ollama, or deterministic Mock mode for offline zero-cost execution.
- **Pluggable Search Providers**: Abstraction supporting Mock, DuckDuckGo, Tavily, and SerpAPI with automatic URL deduplication.
- **Conflict Resolution Engine**: Detects conflicting claims across sources, downgrades suspicious third-party claims, preserves official registry ground truth, and outputs actionable discrepancy warnings.
- **Refresh & Audit History**: Re-researching a company tracks changes field-by-field, logs `added`, `modified`, and `verified_unchanged` states, and preserves historic evidence.
- **Fast Local Web UI**: Built-in dark-mode frontend served directly by FastAPI.

---

## 4. Setup Instructions

### Prerequisites
- Python 3.10+ (tested on Python 3.13)
- Windows, macOS, or Linux

### Installation

1. **Clone or navigate to the project directory**:
   ```bash
   cd Signalpost
   ```

2. **Create and activate a virtual environment (optional but recommended)**:
   ```bash
   # Windows PowerShell
   python -m venv venv
   .\venv\Scripts\Activate.ps1

   # Linux / macOS
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Setup environment variables**:
   ```bash
   cp .env.example .env
   ```

---

## 5. Environment Variables

Configure options in `.env`:

| Variable | Default | Description |
|---|---|---|
| `ENV` | `development` | Environment mode (`development`, `production`, `testing`) |
| `HOST` | `127.0.0.1` | Server binding host |
| `PORT` | `8000` | Server binding port |
| `DATABASE_PATH` | `signalpost.db` | Path to local SQLite database file |
| `LLM_PROVIDER` | `mock` | LLM backend: `mock`, `openai`, `gemini`, `groq`, `openrouter`, `ollama` |
| `LLM_API_KEY` | *(empty)* | API key for LLM provider (not needed for `mock`) |
| `LLM_MODEL` | `gpt-4o-mini` | Model identifier (e.g. `gpt-4o-mini`, `gemini-1.5-flash`, `llama-3.1-70b-versatile`) |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Base URL for OpenAI-compatible endpoints |
| `SEARCH_PROVIDER` | `mock` | Search engine: `mock`, `duckduckgo`, `tavily`, `serpapi` |
| `SEARCH_API_KEY` | *(empty)* | API key for search provider (not needed for `mock` or `duckduckgo`) |
| `BRREG_API_BASE_URL`| `https://data.brreg.no/enhetsregisteret/api` | Official Norwegian Enhetsregisteret API |
| `HTTP_TIMEOUT_SECONDS` | `15` | Default HTTP request timeout |

---

## 6. How to Run Backend

Start the FastAPI application via Uvicorn:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API will be available at:
- **Interactive OpenAPI Docs (Swagger UI)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc Documentation**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **Health Check Endpoint**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## 7. How to Run Frontend

The frontend is served directly by FastAPI at the root URL:

Open your web browser and navigate to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

From the web interface:
1. Click any of the example chips (e.g., **Equinor ASA: 923609016**, **DNB Bank: 984851006**, **Kongsberg Gruppen: 943753709**, **Telenor: 982463718**) or enter any valid 9-digit Norwegian organization number.
2. Click **Research Company**.
3. View the verified identity card, key facts, executive leadership chips, evidence citations, and confidence scores.
4. Click **Refresh** to execute a re-research freshness check.
5. Click **Audit History** to view past research runs and field-level change history.

---

## 8. Batch Research Runner

Prepare a text file with one Norwegian organization number per line, or a CSV/JSON file containing organization numbers. From the project root, run:

```bash
python scripts/run_batch.py --input companies.txt
```

The runner calls the existing `ResearcherService` for each valid number and writes detailed results and a concise summary under `artifacts/`. Each result includes `llm_usage` metadata with the configured provider, provider actually used, whether MockLLMProvider fallback occurred, and the fallback reason. The report summarizes actual provider counts and fallback count. It runs at most three companies simultaneously by default. Set `MAX_CONCURRENCY` or pass `--max-concurrency` to change the limit. Transient failures receive one retry by default; invalid and not-found numbers are not retried.

To validate and count inputs without making research requests or changing the database, run:

```bash
python scripts/run_batch.py --input companies.txt --dry-run
```

`scripts/test_companies.txt` demonstrates the input format. Running it without `--dry-run` performs live research.

---

## 9. How to Run Tests

Run the complete automated test suite with pytest:

```bash
pytest -v
```

The tests validate:
- Norwegian organization number checksums and formatting
- Brønnøysundregistrene response parsing and status mapping
- Identity mismatch flagging and conflict resolution hierarchy
- Evidence validation, URL protocol security, and database deduplication
- End-to-end REST API flows (health, research, get, refresh, history, and error states)
- Batch input parsing, per-company failure isolation, concurrency limits, and result files

---

## 10. Example API Requests

### 1. Research a Company
```bash
curl -X POST "http://127.0.0.1:8000/api/research" \
     -H "Content-Type: application/json" \
     -d '{"company_number": "923609016"}'
```

### 2. Retrieve Stored Company Profile
```bash
curl -X GET "http://127.0.0.1:8000/api/company/923609016"
```

### 3. Refresh Company Profile (Detect Changes)
```bash
curl -X POST "http://127.0.0.1:8000/api/company/923609016/refresh"
```

### 4. View Audit History
```bash
curl -X GET "http://127.0.0.1:8000/api/company/923609016/history"
```

### 5. List All Researched Companies
```bash
curl -X GET "http://127.0.0.1:8000/api/companies?limit=10"
```

---

## 11. Example Response

`POST /api/research` response for **Equinor ASA (923609016)**:

```json
{
  "company_number": "923609016",
  "identity": {
    "company_number": "923609016",
    "name": "EQUINOR ASA",
    "organization_type": "Allmennaksjeselskap",
    "organization_type_code": "ASA",
    "status": "Active",
    "registered_address": "Forusbeen 50",
    "postal_code": "4035",
    "city": "STAVANGER",
    "country": "Norway",
    "official_website": "https://www.equinor.com",
    "founding_date": "1972-09-18",
    "registration_date": "1995-03-12"
  },
  "industry_code": "06.100",
  "industry_description": "Utvinning av råolje",
  "business_purpose": "Selv, eller gjennom deltakelse i eller sammen med andre selskaper å utvikle, produsere og markedsføre ulike former for energi...",
  "employee_count": 21272,
  "financials": {
    "latest_accounts_year": "2025",
    "share_capital": 5976872600.0,
    "currency": "NOK",
    "shares_count": 2390749040
  },
  "management": [
    {
      "role": "Daglig leder / CEO",
      "name": "Anders Opedal"
    },
    {
      "role": "Styrets leder / Board Chair",
      "name": "Jarle Kjell Roth"
    }
  ],
  "recent_activity": "EQUINOR ASA is an enterprise identified in public sources with active business operations.",
  "facts": {
    "company_name": "EQUINOR ASA",
    "company_number": "923609016",
    "status": "Active",
    "organization_type": "Allmennaksjeselskap",
    "registered_address": "Forusbeen 50",
    "city": "STAVANGER",
    "website": "https://www.equinor.com",
    "employee_count": 21272,
    "industry": "Utvinning av råolje"
  },
  "evidence_list": [
    {
      "id": "c1f736c9-0ea7-4ce5-866d-5b32ec3937aa",
      "field": "company_name",
      "value": "EQUINOR ASA",
      "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
      "source_title": "Brønnøysundregistrene (Enhetsregisteret) - 923609016",
      "source_type": "official_registry",
      "retrieved_at": "2026-10-04T06:35:34.502901+00:00",
      "confidence": 1.0,
      "explanation": "Official company name registered in Enhetsregisteret: EQUINOR ASA",
      "is_verified": true
    },
    {
      "id": "e8381273-d5ff-4da7-be70-13f50800c0ba",
      "field": "website",
      "value": "https://www.equinor.com",
      "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
      "source_title": "Brønnøysundregistrene (Enhetsregisteret) - 923609016",
      "source_type": "official_registry",
      "retrieved_at": "2026-10-04T06:35:34.502901+00:00",
      "confidence": 1.0,
      "explanation": "Official website URL registered in Enhetsregisteret",
      "is_verified": true
    }
  ],
  "conflicts": [],
  "warnings": [],
  "overall_confidence": 0.95,
  "research_run_id": "78749c95-3ca3-4a0b-93ff-bb2172782df8",
  "researched_at": "2026-10-04T06:35:36.192305+00:00",
  "last_refreshed_at": null
}
```

---

## 12. How the Research Pipeline Works

```text
       Company Number (e.g. 923609016)
                    │
                    ▼
       ┌──────────────────────────┐
       │   Modulo 11 Validation   │ ──(Invalid)──> Returns 400 Bad Request
       └──────────────────────────┘
                    │ (Valid)
                    ▼
       ┌──────────────────────────┐
       │     Company Resolver     │ <──> Queries Enhetsregisteret & Roller APIs
       └──────────────────────────┘
                    │ Authoritative Identity, NACE, Employees, Roles, Capital
                    ▼
       ┌──────────────────────────┐
       │     Source Discovery     │ <──> Multi-query search (DDG, Tavily, SerpAPI, Mock)
       └──────────────────────────┘
                    │ Deduplicated candidate URLs & public snippets
                    ▼
       ┌──────────────────────────┐
       │   Fact Extraction (LLM)  │ <──> Constrained JSON extraction with citations
       └──────────────────────────┘
                    │ Extracted facts & quotes
                    ▼
       ┌──────────────────────────┐
       │ Identity Verifier &      │ ──> Cross-checks org nr, legal name & domains
       │ Conflict Resolver        │ ──> Applies source hierarchy: Official Registry > Web
       └──────────────────────────┘
                    │ Calibrated Confidence & Discrepancy Alerts
                    ▼
       ┌──────────────────────────┐
       │  Profile & Audit Engine  │ ──> Computes diffs (added / modified / unchanged)
       └──────────────────────────┘
                    │
                    ▼
       ┌──────────────────────────┐
       │    SQLite Persistence    │ ──> Stores company, facts, runs, evidence, history
       └──────────────────────────┘
                    │
                    ▼
       JSON Response & Web UI Render
```

---

## 13. Limitations

1. **Sub-unit (underenheter) Resolution**: This MVP focuses on primary parent enterprise numbers (*hovedenheter*). Branch locations (*underenheter*, e.g., individual store locations) have separate 9-digit identifiers in Enhetsregisteret and can be resolved similarly, but parent-subsidiary trees are not automatically traversed.
2. **Paid Financial Statements**: Basic share capital, number of shares, and latest filed annual accounts year are extracted directly from Enhetsregisteret for free. Detailed multi-year P&L balance sheet tables are behind paid registries (Proff Forvalt / Purehelp) or require PDF scraping of filed annual reports.
3. **Public Search Rate Limits**: When using DuckDuckGo without an API key, excessive parallel requests may trigger temporary anti-bot throttling. For high-throughput automated batch processing, configure `SEARCH_PROVIDER=tavily` or `SEARCH_PROVIDER=serpapi`.

---

## 14. How to Replace Search and LLM Providers

### Switching Search Providers

Signalpost uses a provider factory pattern in [app/services/search_service.py](file:///c:/Users/sruth/OneDrive/Desktop/Signalpost/app/services/search_service.py).

To switch to **Tavily**:
1. Add to `.env`:
   ```ini
   SEARCH_PROVIDER=tavily
   SEARCH_API_KEY=tvly-your-key-here
   ```

To switch to **SerpAPI (Google Search)**:
1. Add to `.env`:
   ```ini
   SEARCH_PROVIDER=serpapi
   SEARCH_API_KEY=your-serpapi-key-here
   ```

To add a new custom search provider (e.g. Bing Search):
1. Subclass `BaseSearchProvider` in `app/services/search_service.py`.
2. Implement `async def search(self, query: str, max_results: int = 5) -> List[SearchResult]`.
3. Register it in `SearchService._init_provider()`.

### Switching LLM Providers

Signalpost isolates LLM integration in [app/services/llm_service.py](file:///c:/Users/sruth/OneDrive/Desktop/Signalpost/app/services/llm_service.py).

To use **OpenAI (GPT-4o / GPT-4o-mini)**:
```ini
LLM_PROVIDER=openai
LLM_API_KEY=sk-proj-your-openai-key
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
```

To use **Google Gemini**:
```ini
LLM_PROVIDER=gemini
LLM_API_KEY=AIzaSy-your-gemini-key
LLM_MODEL=gemini-1.5-flash
```

To use **Groq (Fast Llama-3.1-70B)**:
```ini
LLM_PROVIDER=groq
LLM_API_KEY=gsk_your-groq-key
LLM_MODEL=llama-3.1-70b-versatile
LLM_BASE_URL=https://api.groq.com/openai/v1
```

To use **Local Ollama (100% Private Offline LLM)**:
```ini
LLM_PROVIDER=ollama
LLM_API_KEY=ollama
LLM_MODEL=llama3.1
LLM_BASE_URL=http://localhost:11434/v1
```

To add a new provider:
1. Subclass `BaseLLMProvider` in `app/services/llm_service.py`.
2. Implement `async def extract_facts(...) -> LLMExtractionResult`.
3. Register it in `LLMService._init_provider()`.
