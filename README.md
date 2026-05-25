# SF Rideshare Availability Checker

> A multi-agent FastAPI demo that estimates Uber-style ride availability, surge pricing, and weather risk for San Francisco — all from a single Python file, with no API keys required.

---

## What Is This?

This is a **demonstration web application** built to showcase a **multi-agent architecture** applied to a real-world, relatable problem: checking whether you can get a rideshare in San Francisco right now, and what it might cost.

The app is intentionally simple to read and run. It is designed as a learning tool, architecture walkthrough, and technical interview reference — not a production Uber replacement.

Because Uber and Lyft do not expose unrestricted public APIs for real-time driver availability or surge pricing, the app runs in **mock mode by default**, generating realistic simulated data based on San Francisco geography, time of day, weather conditions, and ride type. A live weather mode using [Open-Meteo](https://open-meteo.com/) (no API key required) is optionally available.

---

## Why This App Exists

Modern AI and automation systems increasingly rely on **multi-agent architectures** — systems where multiple specialized agents each handle a narrow responsibility, and a central orchestrator combines their outputs into a coherent result.

This demo makes that pattern **concrete and tangible** by applying it to something everyone understands: calling a rideshare.

The goals are to:

- Show how an orchestrator coordinates independent agents with different responsibilities
- Demonstrate how agents can pass context to one another (weather affecting availability, availability affecting surge)
- Illustrate how to build a clean, testable multi-agent system without heavyweight frameworks
- Provide a fully runnable app that works out of the box, suitable for demos, walkthroughs, and interviews

---

## What the App Does

A user enters a pickup location, destination, and ride type. The app runs four agents in sequence and returns a structured result:

```
User Request
    │
    ▼
OrchestratorAgent
   ├── 1. WeatherAgent         → current SF weather conditions
   ├── 2. RideAvailabilityAgent → nearby cars + estimated wait (weather-adjusted)
   ├── 3. SurgePricingAgent    → surge multiplier + price band (demand + weather-aware)
   ├── 4. Recommendation       → plain-English advice for the user
   └── 5. Confidence Score     → how reliable is this estimate?
    │
    ▼
Structured JSON Response + Web UI Display
```

### What you see in the result

| Field | Description |
|---|---|
| Availability level | `high` / `medium` / `low` / `unavailable` |
| Nearby cars | Estimated count for the selected ride type |
| Estimated wait | Minutes until pickup |
| Weather condition | Clear, Foggy, Light rain, etc. |
| Weather risk | `low` / `medium` / `high` |
| Surge multiplier | e.g. ×1.0 (normal) to ×3.5+ (very high) |
| Price band | `normal` / `elevated` / `high` / `very_high` |
| Recommendation | Plain-English summary for the rider |
| Confidence score | 0.0–1.0 signal on estimate reliability |

### Supported ride types

- **UberX** — high supply, standard pricing
- **UberXL** — moderate supply, slightly more limited
- **Comfort** — premium economy, fewer cars
- **Black** — lowest supply, highest surge sensitivity

### Default location

The app defaults to **downtown San Francisco** (37.7749, −122.4194) with a destination near the Financial District (37.7840, −122.4090).

---

## Focus of the Demo

The primary focus is **agent orchestration and context passing**, not UI polish or data accuracy.

Key architectural points the demo illustrates:

### 1. Agents as classes with typed interfaces
Each agent is a Python class with a clearly typed async method. Agents are independently testable and replaceable.

### 2. Context flows downstream
The WeatherAgent result is passed into RideAvailabilityAgent. Both results are passed into SurgePricingAgent. Downstream agents make smarter decisions because they have upstream context — this is the core of a useful multi-agent pipeline.

### 3. The orchestrator owns sequencing and synthesis
`OrchestratorAgent` does not do domain work itself. It sequences the agents, combines their outputs, builds the recommendation, and calculates confidence. This separation keeps each agent focused.

### 4. Mock-first, live-ready
The app runs fully without credentials. Live integration points are marked in the code with comments showing exactly where a real Uber, Open-Meteo, or third-party API would connect. Switching to live weather requires only a single environment variable.

### 5. Single-file simplicity
The entire application — agents, models, API, and frontend — lives in `main.py`. This is a deliberate constraint to keep the architecture visible and readable without navigating across files.

---

## Technology Stack

| Layer | Technology | Why |
|---|---|---|
| **Web framework** | [FastAPI](https://fastapi.tiangolo.com/) | Async-native, automatic OpenAPI docs, clean routing |
| **ASGI server** | [Uvicorn](https://www.uvicorn.org/) | Lightweight, works with `--reload` for development |
| **Data validation** | [Pydantic v2](https://docs.pydantic.dev/) | Typed request/response models with field-level validation |
| **HTTP client** | [httpx](https://www.python-httpx.org/) | Async HTTP for optional live weather API calls |
| **Language** | Python 3.11+ | `zoneinfo`, type hints, `async/await` throughout |
| **Frontend** | Inline HTML + CSS + vanilla JS | No build step, no framework, served directly from FastAPI |
| **Weather data** | [Open-Meteo](https://open-meteo.com/) (optional) | Free, no API key, WMO weather codes |
| **Rideshare data** | Mock (default) | No public Uber/Lyft real-time API available |

### Why FastAPI?

FastAPI makes it straightforward to define typed endpoints, validate inputs automatically via Pydantic, and serve both JSON APIs and HTML from the same app. The automatic `/docs` (Swagger UI) at `http://localhost:8000/docs` is a bonus for walkthroughs.

### Why a single file?

A single `main.py` means the entire architecture is visible at once. In a demo or interview context, you can scroll through the file top-to-bottom and explain every layer without switching files. When this pattern needs to scale, the single-file structure maps cleanly onto a standard project layout.

### Why mock data?

Real rideshare APIs require vendor agreements, OAuth flows, and rate limit management — none of which belong in a demo focused on agent architecture. Mock data lets the architecture shine without API setup friction.

---

## Project Structure

```
Multi-Agent-Reservation/
├── main.py        ← entire application (agents, API, frontend)
├── RUNBOOK.md     ← how to install, run, configure, and extend
└── README.md      ← this file
```

---

## Quick Start

```bash
pip install fastapi uvicorn httpx pydantic
uvicorn main:app --reload
```

Then open `http://localhost:8000` in your browser.

For the full setup guide, configuration options, API reference, curl examples, mock behavior tables, and live API extension instructions, see:

---

## Runbook

**[RUNBOOK.md](./RUNBOOK.md)** — the operational reference for this app.

Covers:

- Step-by-step installation and startup
- All three endpoints with example `curl` requests
- Every environment variable and what it controls
- Mock data ranges for cars, wait times, and surge bands
- How to enable live weather (Open-Meteo, no key required)
- Where to plug in a real Uber or Lyft API
- Troubleshooting table for common errors
- Full architecture diagram

---

## Live API Extension Points

The code is annotated with comments marking where real vendor APIs slot in:

- **Weather:** `WeatherAgent._fetch_live_weather()` — currently calls Open-Meteo; swap `WEATHER_API_URL` for any compatible endpoint
- **Ride availability:** `RideAvailabilityAgent.check_availability()` — stub comment shows the Uber Products API call pattern
- **Surge pricing:** `SurgePricingAgent.estimate_surge()` — placeholder for real-time pricing data

> **Note:** Real Uber, Lyft, or rideshare API integration must comply with vendor terms of service, privacy requirements, authentication requirements, rate limits, and data retention policies.

---

## API at a Glance

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `GET` | `/health` | Health check |
| `POST` | `/api/check-ride` | Run all agents, return structured result |
| `GET` | `/docs` | Interactive Swagger UI (auto-generated) |

---

## Security Notes

- No API secrets are hardcoded
- All user inputs are validated by Pydantic before reaching agent logic
- Stack traces are never exposed to the caller
- No location history is stored
- Environment variables are used for all optional credentials
