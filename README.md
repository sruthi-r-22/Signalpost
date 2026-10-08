# Signalpost

Signalpost is an evidence-first Norwegian company intelligence agent. It accepts a Norwegian organization number, resolves the official identity through **Brønnøysundregistrene (Brreg / Enhetsregisteret)**, gathers supplementary public sources, extracts structured information, and assembles a profile with evidence and verification context. Research profiles and run history are persisted for later review.

## Quick Start

From the repository root:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then configure provider credentials as described below. Start the application with its simple entry point:

```bash
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) for the web interface or [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the API documentation. Configure provider credentials in `.env` for live Groq and Tavily research. Mock LLM and search providers are development/testing options; a Tavily configuration without a key uses DuckDuckGo instead.

---

## 1. Project Overview

Norwegian companies are uniquely identified by a 9-digit organization number governed by the Modulo 11 checksum algorithm. Signalpost treats this organization number as the primary identity key and follows an evidence-first pipeline:

**Norwegian organization number → validation → Brreg identity resolution → public-source research → structured extraction → verification and conflict handling → evidence-backed company profile → persistence and history.**

The registry supplies the authoritative identity baseline. Public-source and LLM-derived claims are associated with evidence and subjected to validation and conflict handling; results should be reviewed rather than treated as guaranteed facts.

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
├── tests/                          # Pytest suite for services, API, and batch runner
│
├── run.py                          # Application entry point
├── .env.example                    # Template environment variables
├── requirements.txt                # Python dependencies
└── README.md                       # Comprehensive project documentation
```

---

## 3. Features

- **Strict Norwegian Org Number Validation**: Validates 9 digits, strips extraneous characters, and enforces the standard Modulo 11 weighted checksum (`[3, 2, 7, 6, 5, 4, 3, 2]`).
- **Free, Open Official Registry Resolution**: Directly queries Brønnøysundregistrene's Enhetsregisteret and Roller APIs without requiring external API keys.
- **Evidence-First Architecture**: Facts can be accompanied by source URLs, retrieval timestamps, confidence, source type, and supporting details; evidence validation and verification help assess the claims.
- **Evidence-Grounded Extraction**: The LLM is instructed to use supplied sources, leave unsupported fields null, and cite source URLs. This reduces unsupported claims but is not a guarantee that every output is correct.
- **LLM Integration**: The configured evaluation setup uses Groq with `openai/gpt-oss-120b`. Other supported providers are listed in the environment variables table; MockLLM is selectable for development/testing.
- **Search Providers**: Tavily is the preferred configured live search provider. If selected without a nonblank `SEARCH_API_KEY`, Signalpost automatically uses DuckDuckGo; explicit DuckDuckGo operation also requires no search API key.
- **Conflict Resolution Engine**: Detects conflicting claims across sources, downgrades suspicious third-party claims, preserves official registry ground truth, and outputs actionable discrepancy warnings.
- **Refresh & Audit History**: Re-researching a company tracks changes field-by-field, logs `added`, `modified`, and `verified_unchanged` states, and preserves historic evidence.
- **Fast Local Web UI**: Built-in dark-mode frontend served directly by FastAPI.

---

## 4. Setup and Configuration

### Prerequisites
- Python 3.10+
- Windows, macOS, or Linux

### Installation

1. Navigate to the repository root.
2. (Optional) Create and activate a virtual environment:
   ```bash
   # Windows PowerShell
   python -m venv venv
   .\venv\Scripts\Activate.ps1

   # Linux / macOS
   python -m venv venv
   source venv/bin/activate
   ```
3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and edit it with your provider settings:
   ```bash
   # Windows PowerShell
   Copy-Item .env.example .env

   # Linux / macOS
   cp .env.example .env
   ```

Keep API keys in `.env`; never commit real credentials. For the configured Groq evaluation setup, supply your own `LLM_API_KEY` and use:
```ini
LLM_PROVIDER=groq
LLM_API_KEY=<your-groq-api-key>
LLM_MODEL=openai/gpt-oss-120b
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_TEMPERATURE=0.0
```

Tavily is the preferred configured search provider. Supply your own key:
```ini
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=<your-tavily-api-key>
```

If `SEARCH_PROVIDER=tavily` and `SEARCH_API_KEY` is missing or blank, Signalpost selects DuckDuckGo automatically. DuckDuckGo can also be selected explicitly and needs no search API key. Brreg's public registry API does not require an API key. `LLM_PROVIDER=mock` explicitly selects MockLLM for development/testing.

### Environment Variables

Copy `.env.example` to `.env` as a starting point. The template defaults to Mock providers; set the Groq and Tavily values above for live evaluation. All credentials must be supplied through environment variables or `.env` and kept out of source control.

| Variable | Default | Description |
|---|---|---|
| `ENV` | `development` | Environment mode (`development`, `production`, `testing`) |
| `HOST` | `127.0.0.1` | Server binding host |
| `PORT` | `8000` | Server binding port |
| `DATABASE_PATH` | `signalpost.db` | Path to local SQLite database file |
| `LLM_PROVIDER` | `mock` | LLM backend: `mock`, `openai`, `gemini`, `groq`, `openrouter`, `ollama` |
| `LLM_API_KEY` | *(empty)* | Provider credential; required for configured hosted LLMs such as Groq, not needed for MockLLM |
| `LLM_MODEL` | `gpt-4o-mini` | Model identifier; the configured Groq model is `openai/gpt-oss-120b` |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Base URL for OpenAI-compatible endpoints |
| `LLM_TEMPERATURE` | `0.0` | LLM sampling temperature |
| `SEARCH_PROVIDER` | `mock` | Search engine: `mock`, `duckduckgo`, `tavily`, `serpapi` |
| `SEARCH_API_KEY` | *(empty)* | Tavily or SerpAPI credential; not needed for DuckDuckGo. Tavily with a missing/blank key falls back to DuckDuckGo |
| `BRREG_API_BASE_URL`| `https://data.brreg.no/enhetsregisteret/api` | Official Norwegian Enhetsregisteret API |
| `HTTP_TIMEOUT_SECONDS` | `15` | Default HTTP request timeout |

---

## 5. Running the Application

From the repository root, start the FastAPI application:

```bash
python run.py
```

The interface is served at [http://127.0.0.1:8000](http://127.0.0.1:8000). Interactive API documentation is at `/docs`, ReDoc is at `/redoc`, and the health check is at `/health`. The server host and port are configurable through `.env`.

### Web Interface

The frontend is served by FastAPI at the root URL. Enter a valid Norwegian organization number or choose an example, then start research. The interface presents company identity, facts, evidence, confidence, and available conflict information. Use **Refresh** to research again and **Audit History** to review previous runs and field-level changes.

## 6. Batch Research Runner

Prepare a text file with one Norwegian organization number per line, or a CSV/JSON file containing organization numbers. From the project root, run:

```bash
python scripts/run_batch.py --input <INPUT_FILE> --output-dir artifacts/evaluation
```

Replace `<INPUT_FILE>` with the evaluator's own fresh TXT, CSV, or JSON file. The runner calls `ResearcherService` for each supplied input and writes `batch_results.json` and `batch_report.json` under the output directory (`artifacts/` by default). No repository sample list is required. Supported formats are TXT (one number per line), CSV (a recognized organization-number column or numbers in the first column), and JSON (a list or an object containing `company_numbers`, `organization_numbers`, or `companies`).

Each normally completed input produces one result record. Result statuses distinguish `success`, `invalid`, `not_found`, `failed`, and `rate_limited` where applicable. The default concurrency is one; use `--max-concurrency` or `MAX_CONCURRENCY` to change it. Batch-level retries for transient failures are configurable with `--retries` (default one, in addition to the initial attempt); invalid and not-found inputs are not retried.

Groq HTTP 429 responses are retried by the LLM service with its existing retry/backoff behavior. If Groq remains rate-limited after those retries, that company is marked `rate_limited`, not reported as a successful MockLLM result. With `--stop-on-rate-limit`, the runner also marks work not yet started as rate-limited; without the flag, other companies continue.

To validate and count inputs without making research requests or changing the database, run:

```bash
python scripts/run_batch.py --input companies.txt --dry-run
```

`scripts/test_companies.txt` demonstrates the input format. Running any input without `--dry-run` performs research and may use the configured external services.

Resume an interrupted run from its output directory. Genuine LLM results and explicitly configured MockLLM results are preserved; results produced through a fallback are eligible for re-research:

```bash
python scripts/run_batch.py --input companies.txt --output-dir artifacts/my-batch --resume
```

As an optional development safeguard, `--stop-on-rate-limit` stops processing remaining companies if Groq is rate-limited:

```bash
python scripts/run_batch.py --input companies.txt --output-dir artifacts/my-batch --stop-on-rate-limit
python scripts/run_batch.py --input companies.txt --output-dir artifacts/my-batch --resume --stop-on-rate-limit
```

Batch results and reports are incrementally written as companies complete. A halted run can therefore be resumed using the same input and output directory.

## 7. Tests

Run the complete suite:

```bash
pytest -q
```

The suite covers organization-number validation, registry parsing, provider behavior, structured extraction and citation handling, evidence and database behavior, identity verification and conflict resolution, API flows, batch parsing/progress/resume/rate-limit handling, and activity freshness. Latest verified full-suite result: **93 passed**.

---

## 8. Example API Requests

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

## 9. Example Response

Illustrative `POST /api/research` response shape for **Equinor ASA (923609016)**; returned values depend on current registry and search data:

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

## 10. How the Research Pipeline Works

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
       │     Source Discovery     │ <──> Multi-query search (Tavily or DuckDuckGo fallback)
       └──────────────────────────┘
                    │ Deduplicated candidate URLs & public snippets
                    ▼
       ┌──────────────────────────┐
       │   Fact Extraction (LLM)  │ <──> Constrained JSON extraction with citations
       └──────────────────────────┘
                    │ Extracted facts & quotes, subject to validation
                    ▼
       ┌──────────────────────────┐
       │ Identity Verifier &      │ ──> Cross-checks org nr, legal name & domains
       │ Conflict Resolver        │ ──> Applies source hierarchy: Official Registry > Web
       └──────────────────────────┘
                    │ Confidence indicators & discrepancy alerts for review
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

## 11. Evidence and Reliability

The configured Groq model is `openai/gpt-oss-120b`. Transient HTTP 429 responses are retried (two retries after the initial attempt) using the existing retry/backoff logic, including provider reset guidance when available and a 30-second maximum wait. If Groq continues returning 429, the rate-limit error propagates and the batch records that company as `rate_limited`; it is not turned into a successful MockLLM result. Use `LLM_PROVIDER=mock` to explicitly select MockLLM for development/testing. The existing fallback behavior for other provider failures remains distinct from this persistent-Groq-429 handling.

For structured JSON validation/generation errors, the OpenAI-compatible provider has a separate single retry using prompt-guided JSON. Returned content is parsed and validated locally, and citation URLs are checked against supplied source documents; these checks do not guarantee factual correctness.

Brreg resolution uses the configured `HTTP_TIMEOUT_SECONDS` and reports network and unexpected HTTP errors to the caller; a 404 is reported as not found. Tavily requests use a 15-second HTTPX timeout with connection establishment capped at 5 seconds. Search discovery catches individual provider/query failures and continues with other queries, so some supplementary sources may be absent. When Tavily is selected without a nonblank API key, search uses DuckDuckGo; Brreg resolution remains a separate earlier pipeline stage.

Evidence records associate claims with source URLs and retrieval metadata. Verification checks identity and conflicting claims, prioritizes official registry information over web claims, and exposes discrepancies and confidence for review. Refresh runs compare changed facts and preserve research history and prior evidence for review. Unsupported or missing information should remain null or unreported rather than be invented; results require human review, and these measures do not guarantee accuracy or eliminate hallucinations.

## 12. Evaluator and Submission Notes

The application separates registry resolution, provider-backed source discovery, structured extraction, verification/conflict resolution, and persistence. Live evaluation uses the public Enhetsregisteret API and configured Groq and search providers; Tavily is preferred, with automatic DuckDuckGo fallback if its key is missing or blank. Do not include API keys in a submission.

Research responses and stored profiles expose facts, evidence, citations, conflicts, warnings, and confidence information. SQLite stores profiles and research history. Batch runs write `batch_results.json` and `batch_report.json` to the selected output directory and update those files incrementally. In a normally completed run, every supplied input has exactly one result record; statuses distinguish successes and applicable invalid, not-found, failed, or rate-limited inputs.

Supply an evaluator-owned TXT, CSV, or JSON file containing fresh Norwegian organization numbers; repository sample lists are not required. The primary evaluator command is shown in Section 6.

If the run is interrupted, repeat it with `--resume` and the same input and output directory. Inspect both JSON output files for result statuses and provider usage.

### Existing 100-Company Local Smoke-Test Evidence

The repository includes `artifacts/batch_report_100_success.json`, `artifacts/batch_results.json`, and the corresponding input list, `companies_100.txt`. This local smoke test processed 100 inputs: 100 successful, 0 failed, 0 rate-limited, 100 genuine Groq successes, and 0 MockLLM fallback successes. It is local smoke-test evidence, not the official competition evaluation.

## 13. Limitations

- Live registry, Groq, and search-provider availability, quotas, latency, and rate limits affect live research. Tavily timeouts are bounded, and a failed search query may yield fewer supplementary web sources.
- Unsupported or missing information should remain null or unreported rather than be invented. Evidence and validation do not guarantee accuracy or eliminate hallucinations.
- The workflow resolves the supplied organization number and does not automatically traverse parent/subsidiary trees.
- The registry provides selected financial metadata, not a complete multi-year financial statement analysis.

---

## 14. Services and Evaluation Cost

| Service | Use |
|---|---|
| Brønnøysundregistrene (Brreg / Enhetsregisteret) | Norwegian company identity and registry data |
| Tavily | Preferred configured public-source search; DuckDuckGo is selected automatically if Tavily is configured without a key |
| Groq (`openai/gpt-oss-120b`) | Structured extraction from supplied registry and public-source information |

Using measured usage from 531 genuine Groq runs (188,646 input tokens and 143,028 output tokens) and reference rates of $0.15 per million input tokens and $0.60 per million output tokens, projected Groq costs are approximately **$0.022 for 100 companies** and **$0.215 for 1,000 companies**. These are projections based on measured usage, not guaranteed charges.

Search-provider costs are separate and depend on actual Tavily requests/credits or use of DuckDuckGo. The combined Groq and search-provider cost is not known.

---

## 15. How to Replace Search and LLM Providers

### Switching Search Providers

Signalpost uses a provider factory pattern in [app/services/search_service.py](app/services/search_service.py).

To select **Tavily** (provide a key to use Tavily; without one, SearchService automatically selects DuckDuckGo):
1. Add to `.env`:
   ```ini
   SEARCH_PROVIDER=tavily
   SEARCH_API_KEY=<your-tavily-api-key>
   ```

To explicitly use **DuckDuckGo** without a search API key:
```ini
SEARCH_PROVIDER=duckduckgo
SEARCH_API_KEY=
```

To switch to **SerpAPI (Google Search)**:
1. Add to `.env`:
   ```ini
   SEARCH_PROVIDER=serpapi
   SEARCH_API_KEY=<your-serpapi-api-key>
   ```

To add a new custom search provider (e.g. Bing Search):
1. Subclass `BaseSearchProvider` in `app/services/search_service.py`.
2. Implement `async def search(self, query: str, max_results: int = 5) -> List[SearchResult]`.
3. Register it in `SearchService._init_provider()`.

### Switching LLM Providers

Signalpost isolates LLM integration in [app/services/llm_service.py](app/services/llm_service.py).

To use **OpenAI (GPT-4o / GPT-4o-mini)**:
```ini
LLM_PROVIDER=openai
LLM_API_KEY=<your-provider-api-key>
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
```

To use **Google Gemini**:
```ini
LLM_PROVIDER=gemini
LLM_API_KEY=<your-provider-api-key>
LLM_MODEL=gemini-1.5-flash
```

To use the tested **Groq** configuration:
```ini
LLM_PROVIDER=groq
LLM_API_KEY=<your-groq-api-key>
LLM_MODEL=openai/gpt-oss-120b
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_TEMPERATURE=0.0
```

For local development/testing without a hosted LLM, explicitly set `LLM_PROVIDER=mock`. Persistent Groq HTTP 429 errors are reported as rate-limited rather than converted into MockLLM success.

To use **Local Ollama**:
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
