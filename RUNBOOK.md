# SF Rideshare Availability Checker — Runbook

## What It Is

A single-file FastAPI web app that estimates Uber-style rideshare availability for San Francisco using a four-agent architecture:

| Agent | Role |
|---|---|
| `WeatherAgent` | Gets or simulates weather at the pickup location |
| `RideAvailabilityAgent` | Estimates nearby cars and wait time |
| `SurgePricingAgent` | Calculates surge multiplier and price band |
| `OrchestratorAgent` | Coordinates all agents and builds the final response |

All agents run in mock mode by default — **no API keys required**.

---

## Quick Start

### 1. Install dependencies

```bash
pip install fastapi uvicorn httpx pydantic
```

### 2. Run the app

```bash
uvicorn main:app --reload
```

Or run directly:

```bash
python main.py
```

### 3. Open the browser

```
http://localhost:8000
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | HTML web UI |
| `GET` | `/health` | Health check |
| `POST` | `/api/check-ride` | Ride availability API |

### Example API call

```bash
curl -s -X POST http://localhost:8000/api/check-ride \
  -H "Content-Type: application/json" \
  -d '{
    "pickup_lat": 37.7749,
    "pickup_lng": -122.4194,
    "destination_lat": 37.7840,
    "destination_lng": -122.4090,
    "ride_type": "UberX"
  }' | python -m json.tool
```

### Supported ride types

- `UberX`
- `UberXL`
- `Comfort`
- `Black`

---

## Environment Variables

All variables are optional. The app runs fully without them.

| Variable | Default | Description |
|---|---|---|
| `USE_LIVE_WEATHER` | `false` | Set `true` to fetch real weather from Open-Meteo (no key needed) |
| `WEATHER_API_URL` | Open-Meteo URL | Override the weather API endpoint |
| `USE_LIVE_UBER` | `false` | Reserved for future live Uber integration |
| `UBER_API_TOKEN` | _(empty)_ | Uber API token (not yet wired; see code comments) |
| `APP_ENV` | `local` | Runtime environment label (`local`, `staging`, `prod`) |

### Enable live weather

```bash
USE_LIVE_WEATHER=true uvicorn main:app --reload
```

Live weather uses [Open-Meteo](https://open-meteo.com/) which requires no API key.  
If the live call fails, the app automatically falls back to mock weather.

---

## Mock Behavior Reference

### Nearby cars by ride type

| Ride Type | Base Range |
|---|---:|
| UberX | 20–60 |
| UberXL | 8–25 |
| Comfort | 10–30 |
| Black | 3–15 |

Cars are reduced by weather risk and late-night hours.

### Wait time by availability level

| Level | Wait |
|---|---:|
| high | 2–5 min |
| medium | 6–10 min |
| low | 11–18 min |
| unavailable | 20–35 min |

### Surge multiplier by price band

| Band | Multiplier |
|---|---:|
| normal | 1.0–1.2× |
| elevated | 1.3–1.6× |
| high | 1.7–2.2× |
| very_high | 2.3–3.5× |

Surge increases for: rain, high wind, low supply, peak commute hours (7–9 AM, 4:30–7 PM, 10 PM–1 AM SF time), and premium ride types.

### Confidence score

Starts at `0.75` (mock data ceiling) and adjusts:

- Low availability: −0.10
- Unavailable: −0.15
- Medium weather risk: −0.05
- High weather risk: −0.10
- High/very-high surge: −0.10
- Live weather successful: +0.05

Range is clamped to `0.35–0.90`.

---

## Extending to Live APIs

All live integration points are marked with comments in `main.py`.

### Plugging in a real weather API

1. Set `USE_LIVE_WEATHER=true`
2. The app calls Open-Meteo by default; replace `WEATHER_API_URL` to point at any compatible endpoint
3. Edit `WeatherAgent._fetch_live_weather()` to adapt the response shape

### Plugging in Uber / Lyft

1. Set `USE_LIVE_UBER=true` and `UBER_API_TOKEN=<your-token>`
2. Edit `RideAvailabilityAgent.check_availability()` — the stub comment shows where to add the real API call
3. Mirror the same pattern in `SurgePricingAgent` for real surge data

> **Important:** Real rideshare API integration must comply with vendor terms of service, privacy requirements, authentication requirements, rate limits, and data retention policies.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install fastapi uvicorn httpx pydantic` |
| Port 8000 already in use | `uvicorn main:app --port 8080 --reload` |
| Live weather always falls back to mock | Check your network; Open-Meteo is public but may be blocked on some networks |
| Validation error on ride type | Use exactly: `UberX`, `UberXL`, `Comfort`, or `Black` |
| Validation error on coordinates | Lat must be −90 to 90; lng must be −180 to 180 |

---

## Architecture Diagram

```
Browser / API Client
        │
        ▼
  FastAPI  POST /api/check-ride
        │
        ▼
  OrchestratorAgent.run()
   ├── 1. WeatherAgent.get_weather()
   ├── 2. RideAvailabilityAgent.check_availability(weather)
   ├── 3. SurgePricingAgent.estimate_surge(availability, weather)
   ├── 4. _build_recommendation(...)
   └── 5. _calculate_confidence(...)
        │
        ▼
  RideCheckResponse (JSON)
```
