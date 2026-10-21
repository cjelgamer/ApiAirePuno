"""Independent Puno PM2.5 estimate API backed by the CAMS global forecast."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "0.1.0"
CITY = "Puno"
SITE_ID = "puno_city"
LATITUDE = -15.8402
LONGITUDE = -70.0219
TIMEZONE = ZoneInfo("America/Lima")
UPSTREAM = "https://air-quality-api.open-meteo.com/v1/air-quality"
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "900"))
cache_lock = threading.Lock()
cache: dict[str, object] = {"expires": 0.0, "payload": None}

app = FastAPI(
    title="API Aire Puno",
    version=APP_VERSION,
    description=(
        "Servicio independiente de estimaciones de PM2.5 para la ciudad de Puno. "
        "La fuente es el pronóstico global CAMS, consultado mediante Open-Meteo; "
        "no representa una medición local."
    ),
)

origins = [
    value.strip()
    for value in os.getenv(
        "AIRPREDICT_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000,https://airpredict-4u3c.onrender.com",
    ).split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def fetch_cams_forecast() -> dict:
    """Fetch an hourly view of the global CAMS forecast for Puno."""
    query = urlencode(
        {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "hourly": "pm2_5",
            "forecast_hours": 48,
            "timezone": "America/Lima",
            "domains": "cams_global",
            "cell_selection": "land",
        }
    )
    request = Request(
        f"{UPSTREAM}?{query}",
        headers={"Accept": "application/json", "User-Agent": "ApiAirePuno/0.1"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"El proveedor respondió HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("No se pudo consultar el proveedor de pronósticos.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("El proveedor devolvió una respuesta no válida.") from None

    times = (payload.get("hourly") or {}).get("time")
    values = (payload.get("hourly") or {}).get("pm2_5")
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        raise RuntimeError("No hay una serie horaria de PM2.5 en la respuesta del proveedor.")
    return payload


def forecast_pair(payload: dict, now: datetime) -> tuple[datetime, float, datetime, float]:
    hourly = payload["hourly"]
    points: list[tuple[datetime, float]] = []
    for stamp, raw_value in zip(hourly["time"], hourly["pm2_5"]):
        if raw_value is None:
            continue
        try:
            local_time = datetime.fromisoformat(stamp).replace(tzinfo=TIMEZONE)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            points.append((local_time, value))

    if len(points) < 2:
        raise RuntimeError("El proveedor no entregó suficientes horas con PM2.5.")

    # Use the first forecast hour at or after now and the closest available
    # value 24 hours later. This is model output, not a sensor reading.
    reference = next((point for point in points if point[0] >= now), None)
    if reference is None:
        reference = points[-2]
    target_time = reference[0] + timedelta(hours=24)
    target = min(points, key=lambda point: abs((point[0] - target_time).total_seconds()))
    return reference[0], reference[1], target[0], target[1]


def get_cached_payload() -> dict:
    now_monotonic = time.monotonic()
    with cache_lock:
        if cache["payload"] is not None and now_monotonic < float(cache["expires"]):
            return cache["payload"]  # type: ignore[return-value]
    try:
        payload = fetch_cams_forecast()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    with cache_lock:
        cache["payload"] = payload
        cache["expires"] = now_monotonic + CACHE_SECONDS
    return payload


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "api-aire-puno", "version": APP_VERSION}


@app.get("/api/puno/estimate")
def estimate_for_puno() -> dict:
    """Return a next-day estimate in AirPredict's general-prediction response shape."""
    payload = get_cached_payload()
    now = datetime.now(TIMEZONE)
    try:
        reference_time, _, target_time, predicted = forecast_pair(payload, now)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None

    upstream = payload.get("hourly_units", {}).get("pm2_5", "µg/m³")
    if upstream not in {"µg/m³", "μg/m³", "ug/m3"}:
        raise HTTPException(502, "El proveedor devolvió unidades de PM2.5 no compatibles.")

    return {
        "mode": "forecast",
        "is_current_forecast": True,
        "site_id": SITE_ID,
        "model_version": "CAMS-global-via-Open-Meteo",
        "units": "µg/m³",
        "predicted_pm25": max(0.0, predicted),
        "observed_date": reference_time.date().isoformat(),
        "target_date": target_time.date().isoformat(),
        "input_origin": "external_model_estimate",
        "outside_training_period": True,
        "note": (
            f"Estimación del pronóstico global CAMS consultado vía Open-Meteo para {CITY}; "
            f"coordenada de la celda de referencia: {payload.get('latitude')}, "
            f"{payload.get('longitude')}. No hay medición local ni validación en Puno. "
            "El modelo global tiene resolución aproximada de 45 km y salida de origen "
            "cada 3 horas. Fuente: CAMS vía Open-Meteo (CC BY 4.0)."
        ),
    }


@app.get("/api/puno/forecast")
def forecast_for_puno() -> dict:
    """Return the provider's hourly forecast for clients that need a time series."""
    payload = get_cached_payload()
    hourly = payload["hourly"]
    values = [
        {"timestamp": stamp, "pm25": value}
        for stamp, value in zip(hourly["time"], hourly["pm2_5"])
        if value is not None
    ]
    return {
        "site_id": SITE_ID,
        "site": CITY,
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "timezone": payload.get("timezone"),
        "units": payload.get("hourly_units", {}).get("pm2_5", "µg/m³"),
        "source": "CAMS global forecast via Open-Meteo",
        "is_measurement": False,
        "history": values,
        "note": "Valores modelados por CAMS, no mediciones de una estación de Puno.",
    }

# Request shape compatible with AirPredict's GeneralScenario input.
from datetime import date
from pydantic import BaseModel, ConfigDict, Field


class GeneralScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str
    observed_date: date
    pm25: float = Field(ge=0, le=3000)
    previous_pm25: float | None = Field(default=None, ge=0, le=3000)


@app.post("/api/general/predict")
def compatible_general_prediction(request: GeneralScenario) -> dict:
    """Accept AirPredict's general scenario request shape for the Puno site."""
    if request.site_id not in {"puno_city", "puno"}:
        raise HTTPException(422, "Esta API solo estima para la ciudad de Puno.")
    today = datetime.now(TIMEZONE).date()
    if request.observed_date != today:
        raise HTTPException(
            422,
            "El escenario CAMS de Puno solo admite la fecha local de hoy; "
            "use GET /api/puno/estimate para la estimación automática.",
        )

    payload = get_cached_payload()
    try:
        reference_time, cams_reference, target_time, cams_target = forecast_pair(
            payload, datetime.now(TIMEZONE)
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None

    # Bias-adjust the CAMS next-day change by the supplied scenario value.
    # The supplied value is user-entered and is not verified as a measurement.
    prediction = max(0.0, request.pm25 + (cams_target - cams_reference))
    return {
        "mode": "scenario",
        "is_current_forecast": False,
        "site_id": "puno_city",
        "model_version": "CAMS-global-via-Open-Meteo",
        "units": "µg/m³",
        "predicted_pm25": prediction,
        "observed_date": request.observed_date.isoformat(),
        "target_date": (request.observed_date + timedelta(days=1)).isoformat(),
        "input_origin": "user_unverified_plus_external_model_trend",
        "outside_training_period": True,
        "note": (
            "Escenario para Puno: valor PM2.5 introducido por el usuario más el cambio "
            "previsto por CAMS global en las siguientes 24 horas. No es una medición ni "
            "una predicción calibrada localmente. previous_pm25 se acepta por compatibilidad "
            "de entrada, pero este ajuste de tendencia no lo utiliza."
        ),
    }
