
import os
import json
import logging
import time
from datetime import datetime
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

logging.basicConfig(level=logging.WARNING)

# Prevent HTTP client logs from exposing API request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

WEATHER_API_BASE_URL = "https://api.weatherapi.com/v1"

mcp = FastMCP(
    "Weather Research MCP",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "10000")),
)


# --------------------------------------------------
# Helper functions
# --------------------------------------------------

def get_api_key() -> str:
    """Read the WeatherAPI key from environment variables."""
    api_key = os.environ.get("WEATHER_API_KEY")

    if not api_key:
        raise ValueError("WEATHER_API_KEY is not configured.")

    return api_key


def error_result(message: str) -> str:
    """Return a consistent JSON error response."""
    return json.dumps({"error": message}, indent=2)


def parse_api_error(response: httpx.Response) -> str:
    """Extract a readable error message from WeatherAPI."""
    try:
        data = response.json()
        return data.get("error", {}).get(
            "message",
            "Unknown WeatherAPI error."
        )
    except (ValueError, AttributeError):
        return "Could not read the WeatherAPI error response."


def save_api_log(
    tool_name: str,
    endpoint: str,
    request_params: dict,
    response_body: Any,
    status_code: int | None,
    duration_ms: int,
):
    """
    Save a WeatherAPI request and response to Neon.
    Never store API keys or other secrets.
    """

    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        logging.error(
            "DATABASE_URL is missing. API log was not saved."
        )
        return

    # Remove credentials before storing request parameters.
    sensitive_fields = {
        "key",
        "api_key",
        "token",
        "password",
        "authorization",
    }

    safe_params = {
        key: value
        for key, value in request_params.items()
        if key.lower() not in sensitive_fields
    }

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO api_logs (
                        provider,
                        endpoint,
                        request_params,
                        response_body,
                        status_code,
                        tool_name,
                        duration_ms
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "WeatherAPI.com",
                        endpoint,
                        Jsonb(safe_params),
                        Jsonb(response_body),
                        status_code,
                        tool_name,
                        duration_ms,
                    ),
                )

        logging.warning(
            "Saved API log: %s | %s | %s ms",
            tool_name,
            status_code,
            duration_ms,
        )

    except Exception:
        # A database logging failure must not crash weather retrieval.
        logging.exception("Could not save API log to Neon.")


async def call_weather_api(
    tool_name: str,
    endpoint: str,
    params: dict,
):
    """
    Make a WeatherAPI request and automatically log its result.

    Returns:
        response: Original HTTP response.
        data: Parsed JSON response.
    """

    start_time = time.perf_counter()
    status_code = None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{WEATHER_API_BASE_URL}/{endpoint}",
                params=params,
            )

        status_code = response.status_code

        try:
            data = response.json()
        except ValueError:
            data = {
                "error": "WeatherAPI returned an invalid JSON response.",
                "response_text": response.text[:1000],
            }

        duration_ms = int(
            (time.perf_counter() - start_time) * 1000
        )

        save_api_log(
            tool_name=tool_name,
            endpoint=endpoint,
            request_params=params,
            response_body=data,
            status_code=status_code,
            duration_ms=duration_ms,
        )

        return response, data

    except httpx.TimeoutException:
        duration_ms = int(
            (time.perf_counter() - start_time) * 1000
        )

        save_api_log(
            tool_name=tool_name,
            endpoint=endpoint,
            request_params=params,
            response_body={"error": "WeatherAPI request timed out."},
            status_code=None,
            duration_ms=duration_ms,
        )

        raise

    except httpx.RequestError:
        duration_ms = int(
            (time.perf_counter() - start_time) * 1000
        )

        save_api_log(
            tool_name=tool_name,
            endpoint=endpoint,
            request_params=params,
            response_body={"error": "Could not connect to WeatherAPI."},
            status_code=None,
            duration_ms=duration_ms,
        )

        raise


# --------------------------------------------------
# Tool 1: Current weather
# --------------------------------------------------

@mcp.tool()
async def get_current_weather(city: str) -> str:
    """
    Get current weather conditions for a location.

    Args:
        city: City or location name to search for.
    """

    city = city.strip()

    if not city:
        return error_result("Please provide a location.")

    try:
        params = {
            "key": get_api_key(),
            "q": city,
            "aqi": "no",
        }

        response, data = await call_weather_api(
            tool_name="get_current_weather",
            endpoint="current.json",
            params=params,
        )

        if response.is_error:
            return json.dumps({
                "error": "WeatherAPI current weather request failed.",
                "status_code": response.status_code,
                "details": parse_api_error(response),
            }, indent=2)

        location = data["location"]
        current = data["current"]

        result = {
            "location": {
                "city": location.get("name"),
                "region": location.get("region"),
                "country": location.get("country"),
                "local_time": location.get("localtime"),
                "timezone": location.get("tz_id"),
            },
            "current_weather": {
                "temperature_c": current.get("temp_c"),
                "feels_like_c": current.get("feelslike_c"),
                "condition": current.get("condition", {}).get("text"),
                "humidity_percent": current.get("humidity"),
                "wind_kph": current.get("wind_kph"),
                "wind_direction": current.get("wind_dir"),
                "precipitation_mm": current.get("precip_mm"),
                "cloud_percent": current.get("cloud"),
                "uv_index": current.get("uv"),
                "is_day": current.get("is_day"),
                "last_updated": current.get("last_updated"),
            },
            "provider": "WeatherAPI.com",
        }

        return json.dumps(result, indent=2)

    except ValueError as exc:
        return error_result(str(exc))

    except httpx.TimeoutException:
        return error_result("WeatherAPI current weather request timed out.")

    except httpx.RequestError:
        return error_result("Could not connect to WeatherAPI.")

    except (KeyError, TypeError):
        logging.exception("Could not process current weather response.")
        return error_result(
            "Could not process the current weather response."
        )


# --------------------------------------------------
# Tool 2: Weather forecast
# --------------------------------------------------

@mcp.tool()
async def get_weather_forecast(city: str, days: int = 3) -> str:
    """
    Get daily weather summaries and morning hourly forecasts.

    Morning is defined as 06:00 through 11:59 local time.
    Past hours are excluded from the forecast.

    Args:
        city: City or location name.
        days: Number of forecast days, from 1 to 3.
    """

    city = city.strip()

    if not city:
        return error_result("Please provide a location.")

    if not isinstance(days, int) or days < 1 or days > 3:
        return error_result(
            "Forecast days must be between 1 and 3."
        )

    try:
        params = {
            "key": get_api_key(),
            "q": city,
            "days": days,
            "aqi": "no",
            "alerts": "no",
        }

        response, data = await call_weather_api(
            tool_name="get_weather_forecast",
            endpoint="forecast.json",
            params=params,
        )

        if response.is_error:
            return json.dumps({
                "error": "WeatherAPI forecast request failed.",
                "status_code": response.status_code,
                "details": parse_api_error(response),
            }, indent=2)

        location = data["location"]
        forecast_days = data["forecast"]["forecastday"]

        location_now = datetime.strptime(
            location["localtime"],
            "%Y-%m-%d %H:%M"
        )

        processed_days = []

        for forecast_day in forecast_days:
            day_data = forecast_day["day"]

            daily_summary = {
                "condition": day_data.get(
                    "condition", {}
                ).get("text"),
                "max_temperature_c": day_data.get("maxtemp_c"),
                "min_temperature_c": day_data.get("mintemp_c"),
                "average_temperature_c": day_data.get("avgtemp_c"),
                "chance_of_rain_percent": day_data.get(
                    "daily_chance_of_rain"
                ),
                "total_precipitation_mm": day_data.get(
                    "totalprecip_mm"
                ),
            }

            morning_hours = []

            for hour in forecast_day.get("hour", []):
                hour_time = datetime.strptime(
                    hour["time"],
                    "%Y-%m-%d %H:%M"
                )

                # Include only 06:00–11:59 local time.
                is_morning = 6 <= hour_time.hour <= 11

                # Exclude hours that have already passed.
                is_future = hour_time > location_now

                if is_morning and is_future:
                    morning_hours.append({
                        "local_time": hour.get("time"),
                        "temperature_c": hour.get("temp_c"),
                        "feels_like_c": hour.get("feelslike_c"),
                        "condition": hour.get(
                            "condition", {}
                        ).get("text"),
                        "chance_of_rain_percent": hour.get(
                            "chance_of_rain"
                        ),
                        "humidity_percent": hour.get("humidity"),
                        "wind_kph": hour.get("wind_kph"),
                    })

            processed_days.append({
                "date": forecast_day.get("date"),
                "daily_summary": daily_summary,
                "morning_hourly_forecast": morning_hours,
            })

        result = {
            "location": {
                "city": location.get("name"),
                "region": location.get("region"),
                "country": location.get("country"),
                "local_time": location.get("localtime"),
                "timezone": location.get("tz_id"),
            },
            "forecast_days": processed_days,
            "provider": "WeatherAPI.com",
        }

        return json.dumps(result, indent=2)

    except ValueError as exc:
        return error_result(str(exc))

    except httpx.TimeoutException:
        return error_result("WeatherAPI forecast request timed out.")

    except httpx.RequestError:
        return error_result("Could not connect to WeatherAPI.")

    except (KeyError, TypeError):
        logging.exception("Could not process forecast response.")
        return error_result(
            "Could not process the forecast response."
        )


# --------------------------------------------------
# Tool 3: Location search
# --------------------------------------------------

@mcp.tool()
async def search_weather_location(query: str) -> str:
    """
    Search for locations using WeatherAPI.

    Returns matching location candidates. If multiple matches
    are found, clarification is required before selecting one.

    Args:
        query: City, town, region, or location search term.
    """

    query = query.strip()

    if not query:
        return error_result(
            "Please provide a location search query."
        )

    try:
        params = {
            "key": get_api_key(),
            "q": query,
        }

        response, data = await call_weather_api(
            tool_name="search_weather_location",
            endpoint="search.json",
            params=params,
        )

        if response.is_error:
            return json.dumps({
                "error": "WeatherAPI location search failed.",
                "status_code": response.status_code,
                "details": parse_api_error(response),
            }, indent=2)

        if not isinstance(data, list):
            return error_result(
                "WeatherAPI returned an unexpected location search response."
            )

        locations = []

        for item in data:
            locations.append({
                "city": item.get("name"),
                "region": item.get("region"),
                "country": item.get("country"),
                "latitude": item.get("lat"),
                "longitude": item.get("lon"),
                "timezone": item.get("tz_id"),
                "location_id": item.get("id"),
            })

        result = {
            "search_query": query,
            "matches_found": len(locations),
            "locations": locations,
            "clarification_required": len(locations) > 1,
            "provider": "WeatherAPI.com",
        }

        return json.dumps(result, indent=2)

    except ValueError as exc:
        return error_result(str(exc))

    except httpx.TimeoutException:
        return error_result("WeatherAPI location search timed out.")

    except httpx.RequestError:
        return error_result("Could not connect to WeatherAPI.")

    except (KeyError, TypeError):
        logging.exception(
            "Unexpected WeatherAPI location search response."
        )
        return error_result(
            "Could not process the location search response."
        )


# --------------------------------------------------
# Start MCP server
# --------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
