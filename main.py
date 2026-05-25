# =============================================================================
# Imports
# =============================================================================
import os
import random
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator, model_validator

# =============================================================================
# Configuration
# =============================================================================
USE_LIVE_WEATHER: bool = os.getenv("USE_LIVE_WEATHER", "false").lower() == "true"
WEATHER_API_URL: str = os.getenv("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast")
USE_LIVE_UBER: bool = os.getenv("USE_LIVE_UBER", "false").lower() == "true"
UBER_API_TOKEN: Optional[str] = os.getenv("UBER_API_TOKEN")
APP_ENV: str = os.getenv("APP_ENV", "local")

# NOTE: Real Uber, Lyft, or rideshare API integration must comply with vendor terms of service,
# privacy requirements, authentication requirements, rate limits, and data retention policies.

SF_TZ = ZoneInfo("America/Los_Angeles")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
VALID_RIDE_TYPES = {"UberX", "UberXL", "Comfort", "Black"}

BASE_CARS = {
    "UberX": (20, 60),
    "UberXL": (8, 25),
    "Comfort": (10, 30),
    "Black": (3, 15),
}

WAIT_RANGES = {
    "high": (2, 5),
    "medium": (6, 10),
    "low": (11, 18),
    "unavailable": (20, 35),
}

SURGE_BANDS = {
    "normal": (1.0, 1.2),
    "elevated": (1.3, 1.6),
    "high": (1.7, 2.2),
    "very_high": (2.3, 3.5),
}

SF_CONDITIONS = ["Clear", "Partly cloudy", "Foggy", "Windy", "Light rain", "Heavy rain"]

# =============================================================================
# Pydantic Models
# =============================================================================

class RideCheckRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    destination_lat: float
    destination_lng: float
    ride_type: str = "UberX"

    @field_validator("pickup_lat", "destination_lat")
    @classmethod
    def validate_lat(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @field_validator("pickup_lng", "destination_lng")
    @classmethod
    def validate_lng(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("Longitude must be between -180 and 180")
        return v

    @field_validator("ride_type")
    @classmethod
    def validate_ride_type(cls, v: str) -> str:
        if v not in VALID_RIDE_TYPES:
            raise ValueError(f"ride_type must be one of: {', '.join(sorted(VALID_RIDE_TYPES))}")
        return v


class RideAvailabilityResult(BaseModel):
    nearby_cars: int
    estimated_wait_minutes: int
    availability_level: str


class WeatherResult(BaseModel):
    temperature_f: float
    condition: str
    wind_mph: float
    is_raining: bool
    weather_risk: str


class SurgePricingResult(BaseModel):
    surge_multiplier: float
    price_band: str
    surge_reason: str


class RideCheckResponse(BaseModel):
    city: str
    ride_type: str
    availability: RideAvailabilityResult
    weather: WeatherResult
    surge: SurgePricingResult
    recommendation: str
    confidence_score: float


# =============================================================================
# Utility Functions
# =============================================================================

def sf_now() -> datetime:
    """Return current datetime in San Francisco local time."""
    return datetime.now(SF_TZ)


def is_peak_hour(dt: datetime) -> bool:
    """Return True if dt falls within SF peak demand hours."""
    h = dt.hour
    m = dt.minute
    # Morning commute: 7:00-9:00
    if 7 <= h < 9:
        return True
    # Evening commute: 16:30-19:00
    if h == 16 and m >= 30:
        return True
    if 17 <= h < 19:
        return True
    # Late-night demand: 22:00-01:00
    if h >= 22 or h < 1:
        return True
    return False


def is_late_night(dt: datetime) -> bool:
    """Return True between midnight and 5 AM SF local time."""
    return 0 <= dt.hour < 5


def celsius_to_fahrenheit(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


# =============================================================================
# Agent: WeatherAgent
# =============================================================================

class WeatherAgent:
    """Retrieves or simulates weather for the pickup location."""

    async def get_weather(self, pickup_lat: float, pickup_lng: float) -> WeatherResult:
        """Return weather for the given coordinates, live or mocked."""
        if USE_LIVE_WEATHER:
            result = await self._fetch_live_weather(pickup_lat, pickup_lng)
            if result:
                logger.info("Live weather fetched successfully")
                return result
            logger.warning("Live weather fetch failed, falling back to mock")
        return self._mock_weather()

    async def _fetch_live_weather(self, lat: float, lng: float) -> Optional[WeatherResult]:
        """
        Fetch real weather from Open-Meteo (no API key required).
        Extension point: swap WEATHER_API_URL for another provider if needed.
        """
        try:
            params = {
                "latitude": lat,
                "longitude": lng,
                "current": ["temperature_2m", "wind_speed_10m", "weather_code"],
                "temperature_unit": "celsius",
                "wind_speed_unit": "mph",
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(WEATHER_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                current = data.get("current", {})
                temp_c: float = current.get("temperature_2m", 15.0)
                wind_mph: float = current.get("wind_speed_10m", 8.0)
                wcode: int = current.get("weather_code", 0)

                # WMO weather interpretation codes
                is_raining = wcode in {51, 53, 55, 61, 63, 65, 80, 81, 82}
                is_heavy_rain = wcode in {65, 82}
                is_foggy = wcode in {45, 48}
                is_snowing = wcode in {71, 73, 75, 77, 85, 86}

                if is_heavy_rain:
                    condition = "Heavy rain"
                elif is_raining:
                    condition = "Light rain"
                elif is_foggy:
                    condition = "Foggy"
                elif wcode in {1, 2, 3}:
                    condition = "Partly cloudy"
                elif wind_mph > 20:
                    condition = "Windy"
                else:
                    condition = "Clear"

                weather_risk = self._calculate_weather_risk(is_raining, wind_mph, is_heavy_rain)
                return WeatherResult(
                    temperature_f=celsius_to_fahrenheit(temp_c),
                    condition=condition,
                    wind_mph=round(wind_mph, 1),
                    is_raining=is_raining,
                    weather_risk=weather_risk,
                )
        except Exception as exc:
            logger.error("Live weather error: %s", exc)
            return None

    def _mock_weather(self) -> WeatherResult:
        """Simulate realistic San Francisco weather."""
        condition = random.choices(
            SF_CONDITIONS,
            weights=[30, 25, 20, 10, 10, 5],
            k=1,
        )[0]
        is_raining = "rain" in condition.lower()
        wind_mph = round(random.uniform(5, 25) if "Windy" in condition else random.uniform(3, 15), 1)
        temp_f = round(random.uniform(50, 70), 1)
        weather_risk = self._calculate_weather_risk(is_raining, wind_mph, condition == "Heavy rain")
        return WeatherResult(
            temperature_f=temp_f,
            condition=condition,
            wind_mph=wind_mph,
            is_raining=is_raining,
            weather_risk=weather_risk,
        )

    @staticmethod
    def _calculate_weather_risk(is_raining: bool, wind_mph: float, heavy_rain: bool) -> str:
        if heavy_rain or (is_raining and wind_mph > 18):
            return "high"
        if is_raining or wind_mph > 20:
            return "medium"
        return "low"


# =============================================================================
# Agent: RideAvailabilityAgent
# =============================================================================

class RideAvailabilityAgent:
    """Estimates nearby car count, wait time, and availability level."""

    async def check_availability(
        self, request: RideCheckRequest, weather: WeatherResult
    ) -> RideAvailabilityResult:
        """
        Simulate or fetch ride availability adjusted for weather and time.
        Extension point: replace mock logic with a real Uber/Lyft availability API call.
        """
        if USE_LIVE_UBER and UBER_API_TOKEN:
            # NOTE: Real Uber, Lyft, or rideshare API integration must comply with vendor terms of service,
            # privacy requirements, authentication requirements, rate limits, and data retention policies.
            # Placeholder for live Uber Products API:
            #   GET https://api.uber.com/v1.2/products?latitude=...&longitude=...
            logger.info("Live Uber mode requested but not implemented; using mock")

        return self._mock_availability(request, weather)

    def _mock_availability(
        self, request: RideCheckRequest, weather: WeatherResult
    ) -> RideAvailabilityResult:
        now = sf_now()
        lo, hi = BASE_CARS[request.ride_type]
        nearby_cars = random.randint(lo, hi)

        # Weather reduction
        if weather.weather_risk == "high":
            nearby_cars = int(nearby_cars * 0.55)
        elif weather.weather_risk == "medium":
            nearby_cars = int(nearby_cars * 0.75)

        # Late-night reduction
        if is_late_night(now):
            nearby_cars = int(nearby_cars * 0.60)

        nearby_cars = max(0, nearby_cars)

        # Determine level thresholds relative to ride type max
        _, type_max = BASE_CARS[request.ride_type]
        ratio = nearby_cars / type_max if type_max else 0

        if ratio >= 0.45:
            level = "high"
        elif ratio >= 0.25:
            level = "medium"
        elif nearby_cars > 0:
            level = "low"
        else:
            level = "unavailable"

        wait_lo, wait_hi = WAIT_RANGES[level]
        wait_min = random.randint(wait_lo, wait_hi)

        return RideAvailabilityResult(
            nearby_cars=nearby_cars,
            estimated_wait_minutes=wait_min,
            availability_level=level,
        )


# =============================================================================
# Agent: SurgePricingAgent
# =============================================================================

class SurgePricingAgent:
    """Estimates surge multiplier and price band using context-aware logic."""

    async def estimate_surge(
        self,
        request: RideCheckRequest,
        availability: RideAvailabilityResult,
        weather: WeatherResult,
    ) -> SurgePricingResult:
        """
        Estimate surge pricing.
        Extension point: replace with a real-time pricing API for live surge data.
        """
        now = sf_now()
        surge_score = 0  # higher score → higher surge band

        reasons = []

        if availability.availability_level == "low":
            surge_score += 2
            reasons.append("low nearby supply")
        elif availability.availability_level == "medium":
            surge_score += 1

        if weather.is_raining:
            surge_score += 2
            reasons.append("rain")
        if weather.wind_mph > 20:
            surge_score += 1
            reasons.append("high wind")

        if is_peak_hour(now):
            surge_score += 2
            reasons.append("peak demand hours")
        elif is_late_night(now):
            surge_score += 1
            reasons.append("late-night demand")

        if request.ride_type in {"Black", "Comfort"}:
            surge_score += 1
            reasons.append("limited premium supply")

        # Map score to band
        if surge_score >= 5:
            band = "very_high"
        elif surge_score >= 3:
            band = "high"
        elif surge_score >= 1:
            band = "elevated"
        else:
            band = "normal"

        lo, hi = SURGE_BANDS[band]
        multiplier = round(random.uniform(lo, hi), 2)
        surge_reason = (
            f"Surge driven by: {', '.join(reasons)}." if reasons else "Normal demand and good nearby supply."
        )

        return SurgePricingResult(
            surge_multiplier=multiplier,
            price_band=band,
            surge_reason=surge_reason,
        )


# =============================================================================
# Agent: OrchestratorAgent
# =============================================================================

class OrchestratorAgent:
    """Coordinates all sub-agents and builds the final ride-check response."""

    def __init__(self) -> None:
        self.weather_agent = WeatherAgent()
        self.availability_agent = RideAvailabilityAgent()
        self.surge_agent = SurgePricingAgent()

    async def run(self, request: RideCheckRequest) -> RideCheckResponse:
        """
        Orchestration order:
        1. Weather Agent
        2. Ride Availability Agent (weather-aware)
        3. Surge Pricing Agent (weather + availability-aware)
        4. Recommendation Builder
        5. Confidence Scoring
        """
        try:
            weather = await self.weather_agent.get_weather(request.pickup_lat, request.pickup_lng)
            availability = await self.availability_agent.check_availability(request, weather)
            surge = await self.surge_agent.estimate_surge(request, availability, weather)
            recommendation = self._build_recommendation(request, availability, weather, surge)
            confidence = self._calculate_confidence(availability, weather, surge)

            return RideCheckResponse(
                city="San Francisco",
                ride_type=request.ride_type,
                availability=availability,
                weather=weather,
                surge=surge,
                recommendation=recommendation,
                confidence_score=confidence,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Orchestrator error: %s", exc)
            raise HTTPException(status_code=500, detail="An unexpected error occurred while checking ride availability.")

    @staticmethod
    def _build_recommendation(
        request: RideCheckRequest,
        availability: RideAvailabilityResult,
        weather: WeatherResult,
        surge: SurgePricingResult,
    ) -> str:
        ride = request.ride_type
        level = availability.availability_level
        wait = availability.estimated_wait_minutes
        risk = weather.weather_risk
        band = surge.price_band
        mult = surge.surge_multiplier

        if level == "unavailable":
            return (
                f"No {ride} cars appear to be available right now. "
                "Consider checking again shortly, switching ride types, or using public transit."
            )

        if level == "high" and band == "normal" and risk == "low":
            return (
                f"{ride} availability looks strong in San Francisco. "
                f"Estimated wait time is {wait} minutes, weather risk is low, and pricing appears normal."
            )

        if level in {"low", "medium"} and band in {"high", "very_high"}:
            return (
                f"Cars are limited and surge is {band.replace('_', ' ')} (×{mult}). "
                "Consider public transit, walking, or checking again in 10–15 minutes if the trip is not urgent."
            )

        if level in {"high", "medium"} and band in {"elevated", "high"}:
            return (
                f"Cars are available, but surge pricing is {band.replace('_', ' ')} (×{mult}) — "
                f"likely due to {weather.condition.lower()} and demand. "
                f"Estimated wait is {wait} minutes."
            )

        if risk == "high":
            return (
                f"Weather risk is high ({weather.condition.lower()}, {weather.wind_mph} mph winds). "
                f"Availability is {level} with a {wait}-minute wait and ×{mult} surge."
            )

        return (
            f"{ride} availability is {level} in San Francisco. "
            f"Estimated wait: {wait} minutes. Surge: ×{mult} ({band.replace('_', ' ')} pricing). "
            f"Weather: {weather.condition}."
        )

    @staticmethod
    def _calculate_confidence(
        availability: RideAvailabilityResult,
        weather: WeatherResult,
        surge: SurgePricingResult,
    ) -> float:
        confidence = 0.75  # mock data ceiling
        if availability.availability_level == "low":
            confidence -= 0.10
        if availability.availability_level == "unavailable":
            confidence -= 0.15
        if weather.weather_risk == "high":
            confidence -= 0.10
        elif weather.weather_risk == "medium":
            confidence -= 0.05
        if surge.price_band in {"high", "very_high"}:
            confidence -= 0.10
        if USE_LIVE_WEATHER:
            confidence = min(confidence + 0.05, 0.90)
        return round(max(0.35, min(confidence, 0.90)), 2)


# =============================================================================
# FastAPI App Setup
# =============================================================================

app = FastAPI(
    title="SF Rideshare Availability Checker",
    description="Multi-agent estimated rideshare availability for San Francisco.",
    version="1.0.0",
)

orchestrator = OrchestratorAgent()

# =============================================================================
# HTML Frontend
# =============================================================================

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SF Rideshare Availability Checker</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f0f4f8;
      color: #1a202c;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2rem 1rem;
    }
    h1 { font-size: 1.75rem; font-weight: 700; color: #2d3748; }
    .subtitle {
      margin-top: .5rem;
      font-size: .9rem;
      color: #718096;
      text-align: center;
      max-width: 480px;
    }
    .card {
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 20px rgba(0,0,0,.08);
      padding: 2rem;
      width: 100%;
      max-width: 520px;
      margin-top: 1.5rem;
    }
    label { display: block; font-size: .85rem; font-weight: 600; color: #4a5568; margin-bottom: .25rem; margin-top: 1rem; }
    label:first-of-type { margin-top: 0; }
    input, select {
      width: 100%;
      padding: .6rem .8rem;
      border: 1px solid #cbd5e0;
      border-radius: 8px;
      font-size: .95rem;
      transition: border-color .2s;
      background: #f7fafc;
    }
    input:focus, select:focus { outline: none; border-color: #667eea; background: #fff; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    button {
      margin-top: 1.5rem;
      width: 100%;
      padding: .75rem;
      background: #667eea;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background .2s;
    }
    button:hover { background: #5a67d8; }
    button:disabled { background: #a0aec0; cursor: not-allowed; }
    .result-card {
      margin-top: 1.5rem;
      display: none;
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 20px rgba(0,0,0,.08);
      padding: 1.5rem 2rem;
      width: 100%;
      max-width: 520px;
    }
    .result-card h2 { font-size: 1.1rem; color: #2d3748; margin-bottom: 1rem; }
    .badge {
      display: inline-block;
      padding: .25rem .65rem;
      border-radius: 999px;
      font-size: .78rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .high   { background: #c6f6d5; color: #22543d; }
    .medium { background: #fefcbf; color: #744210; }
    .low    { background: #fed7d7; color: #742a2a; }
    .unavailable { background: #e2e8f0; color: #4a5568; }
    .normal   { background: #c6f6d5; color: #22543d; }
    .elevated { background: #fefcbf; color: #744210; }
    .very_high { background: #fed7d7; color: #742a2a; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: .6rem 1.5rem; margin: .75rem 0; }
    .stat-label { font-size: .78rem; color: #718096; }
    .stat-value { font-size: .95rem; font-weight: 600; color: #2d3748; }
    .recommendation {
      margin-top: 1rem;
      padding: 1rem;
      background: #ebf4ff;
      border-left: 4px solid #667eea;
      border-radius: 4px;
      font-size: .9rem;
      color: #2a4365;
      line-height: 1.55;
    }
    .confidence-bar-wrap { margin-top: .75rem; }
    .confidence-bar-bg { background: #e2e8f0; border-radius: 999px; height: 8px; }
    .confidence-bar { height: 8px; border-radius: 999px; background: #667eea; transition: width .5s; }
    .error-msg {
      margin-top: 1rem;
      padding: .9rem 1.1rem;
      background: #fff5f5;
      border: 1px solid #fc8181;
      border-radius: 8px;
      color: #c53030;
      font-size: .9rem;
      display: none;
    }
    .section-title { font-size: .8rem; font-weight: 700; color: #a0aec0; text-transform: uppercase; letter-spacing: .05em; margin: 1rem 0 .4rem; }
    .demo-note { font-size: .78rem; color: #a0aec0; text-align: center; margin-top: .75rem; }
  </style>
</head>
<body>
  <h1>SF Rideshare Availability</h1>
  <p class="subtitle">Multi-agent estimated ride availability for San Francisco — demo only, not official Uber data.</p>

  <div class="card">
    <div class="section-title">Pickup Location</div>
    <div class="row">
      <div>
        <label for="pickup_lat">Latitude</label>
        <input id="pickup_lat" type="number" step="any" value="37.7749" />
      </div>
      <div>
        <label for="pickup_lng">Longitude</label>
        <input id="pickup_lng" type="number" step="any" value="-122.4194" />
      </div>
    </div>

    <div class="section-title" style="margin-top:1.25rem">Destination</div>
    <div class="row">
      <div>
        <label for="dest_lat">Latitude</label>
        <input id="dest_lat" type="number" step="any" value="37.7840" />
      </div>
      <div>
        <label for="dest_lng">Longitude</label>
        <input id="dest_lng" type="number" step="any" value="-122.4090" />
      </div>
    </div>

    <label for="ride_type">Ride Type</label>
    <select id="ride_type">
      <option>UberX</option>
      <option>UberXL</option>
      <option>Comfort</option>
      <option>Black</option>
    </select>

    <button id="check-btn" onclick="checkRide()">Check Ride Availability</button>
    <p class="demo-note">Estimates use simulated data. Results vary each refresh.</p>
  </div>

  <div class="error-msg" id="error-msg"></div>

  <div class="result-card" id="result-card">
    <h2 id="rc-title">Results</h2>

    <div class="section-title">Availability</div>
    <div class="grid">
      <div><div class="stat-label">Level</div><div class="stat-value" id="rc-level"></div></div>
      <div><div class="stat-label">Nearby Cars</div><div class="stat-value" id="rc-cars"></div></div>
      <div><div class="stat-label">Est. Wait</div><div class="stat-value" id="rc-wait"></div></div>
    </div>

    <div class="section-title">Weather</div>
    <div class="grid">
      <div><div class="stat-label">Condition</div><div class="stat-value" id="rc-cond"></div></div>
      <div><div class="stat-label">Temperature</div><div class="stat-value" id="rc-temp"></div></div>
      <div><div class="stat-label">Wind</div><div class="stat-value" id="rc-wind"></div></div>
      <div><div class="stat-label">Weather Risk</div><div class="stat-value" id="rc-wrisk"></div></div>
    </div>

    <div class="section-title">Surge Pricing</div>
    <div class="grid">
      <div><div class="stat-label">Multiplier</div><div class="stat-value" id="rc-mult"></div></div>
      <div><div class="stat-label">Price Band</div><div class="stat-value" id="rc-band"></div></div>
    </div>
    <div style="font-size:.85rem;color:#718096;margin-top:.3rem" id="rc-reason"></div>

    <div class="recommendation" id="rc-rec"></div>

    <div class="section-title">Confidence</div>
    <div class="confidence-bar-wrap">
      <div style="display:flex;justify-content:space-between;margin-bottom:.3rem">
        <span class="stat-label">Score</span>
        <span class="stat-value" id="rc-conf"></span>
      </div>
      <div class="confidence-bar-bg"><div class="confidence-bar" id="rc-conf-bar"></div></div>
    </div>
  </div>

  <script>
    async function checkRide() {
      const btn = document.getElementById('check-btn');
      const errEl = document.getElementById('error-msg');
      const resCard = document.getElementById('result-card');
      errEl.style.display = 'none';
      resCard.style.display = 'none';
      btn.disabled = true;
      btn.textContent = 'Checking…';

      const body = {
        pickup_lat: parseFloat(document.getElementById('pickup_lat').value),
        pickup_lng: parseFloat(document.getElementById('pickup_lng').value),
        destination_lat: parseFloat(document.getElementById('dest_lat').value),
        destination_lng: parseFloat(document.getElementById('dest_lng').value),
        ride_type: document.getElementById('ride_type').value,
      };

      try {
        const resp = await fetch('/api/check-ride', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail || 'Unknown error');
        }
        renderResult(data);
      } catch (e) {
        errEl.textContent = 'Error: ' + e.message;
        errEl.style.display = 'block';
      } finally {
        btn.disabled = false;
        btn.textContent = 'Check Ride Availability';
      }
    }

    function badge(text, cls) {
      return `<span class="badge ${cls}">${text}</span>`;
    }

    function renderResult(d) {
      const av = d.availability;
      const wx = d.weather;
      const sg = d.surge;
      const lvl = av.availability_level;
      const band = sg.price_band;

      document.getElementById('rc-title').textContent = d.city + ' — ' + d.ride_type;
      document.getElementById('rc-level').innerHTML = badge(lvl, lvl);
      document.getElementById('rc-cars').textContent = av.nearby_cars + ' cars';
      document.getElementById('rc-wait').textContent = av.estimated_wait_minutes + ' min';
      document.getElementById('rc-cond').textContent = wx.condition;
      document.getElementById('rc-temp').textContent = wx.temperature_f + '°F';
      document.getElementById('rc-wind').textContent = wx.wind_mph + ' mph';
      document.getElementById('rc-wrisk').innerHTML = badge(wx.weather_risk, wx.weather_risk === 'low' ? 'high' : (wx.weather_risk === 'medium' ? 'medium' : 'low'));
      document.getElementById('rc-mult').textContent = '×' + sg.surge_multiplier.toFixed(2);
      document.getElementById('rc-band').innerHTML = badge(band.replace('_', ' '), band);
      document.getElementById('rc-reason').textContent = sg.surge_reason;
      document.getElementById('rc-rec').textContent = d.recommendation;

      const pct = Math.round(d.confidence_score * 100);
      document.getElementById('rc-conf').textContent = pct + '%';
      document.getElementById('rc-conf-bar').style.width = pct + '%';

      document.getElementById('result-card').style.display = 'block';
    }
  </script>
</body>
</html>"""


# =============================================================================
# API Routes
# =============================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """Service health check."""
    return {"status": "ok", "service": "san-francisco-rideshare-availability-checker"}


@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def web_ui():
    """Serve the HTML frontend."""
    return HTMLResponse(content=HTML_PAGE)


@app.post("/api/check-ride", response_model=RideCheckResponse, tags=["Rideshare"])
async def check_ride(request: RideCheckRequest):
    """
    Check estimated rideshare availability for a San Francisco trip.

    Coordinates all sub-agents (Weather, RideAvailability, SurgePricing)
    via the OrchestratorAgent and returns a structured result.
    """
    return await orchestrator.run(request)


# =============================================================================
# Local Entrypoint
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
