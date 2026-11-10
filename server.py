
import os
import json
import logging

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load local environment variables from .env
load_dotenv()

# Avoid logging HTTP request URLs, which may contain the API key
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Create MCP server
mcp = FastMCP(
    "Weather Research MCP",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "10000")),
)


@mcp.tool()
async def get_current_weather(city: str) -> str:
    """Get current weather conditions for a city using WeatherAPI.com."""

    api_key = os.environ.get("WEATHER_API_KEY")

    if not api_key:
        return json.dumps({
            "error": "WEATHER_API_KEY is not configured."
        })

    url = "https://api.weatherapi.com/v1/current.json"

    params = {
        "key": api_key,
        "q": city,
        "aqi": "no",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params)

            if response.is_error:
                try:
                    error_data = response.json()
                except ValueError:
                    error_data = {"message": "Weather provider returned an error."}

                logging.error(
                    "WeatherAPI request failed with status %s",
                    response.status_code,
                )

                return json.dumps({
                    "error": "WeatherAPI request failed.",
                    "status_code": response.status_code,
                    "details": error_data.get("error", error_data),
                })

            data = response.json()

            result = {
                "location": {
                    "city": data["location"]["name"],
                    "region": data["location"]["region"],
                    "country": data["location"]["country"],
                    "local_time": data["location"]["localtime"],
                },
                "current_weather": {
                    "temperature_c": data["current"]["temp_c"],
                    "feels_like_c": data["current"]["feelslike_c"],
                    "condition": data["current"]["condition"]["text"],
                    "humidity_percent": data["current"]["humidity"],
                    "wind_kph": data["current"]["wind_kph"],
                    "precipitation_mm": data["current"]["precip_mm"],
                    "uv_index": data["current"]["uv"],
                    "is_day": bool(data["current"]["is_day"]),
                },
                "provider": "WeatherAPI.com",
            }

            return json.dumps(result, indent=2)

    except httpx.TimeoutException:
        return json.dumps({
            "error": "WeatherAPI request timed out. Please try again."
        })

    except httpx.RequestError:
        logging.exception("Could not connect to WeatherAPI")
        return json.dumps({
            "error": "Could not connect to the weather service."
        })

    except (KeyError, ValueError):
        logging.exception("Unexpected WeatherAPI response")
        return json.dumps({
            "error": "Could not process the weather service response."
        })


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
