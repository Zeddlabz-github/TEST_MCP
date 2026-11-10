
import os
import json
import logging

import httpx
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr, not stdout
logging.basicConfig(level=logging.INFO)

# Create MCP server
mcp = FastMCP(
    "Weather Research MCP",
    auth=None,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "10000")),
)



@mcp.tool()
async def get_current_weather(city: str) -> str:
    """
    Get current weather for a city using Open-Meteo.

    Args:
        city: City name, such as Ludhiana, Delhi or London.
    """
    city = city.strip()

    if not city or len(city) > 100:
        return "Please enter a valid city name."

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:

            # Find city coordinates
            geo_response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": city,
                    "count": 1,
                    "language": "en",
                    "format": "json",
                },
            )

            geo_response.raise_for_status()
            geo_data = geo_response.json()

            results = geo_data.get("results")

            if not results:
                return json.dumps({
                    "error": f"No location found for {city}"
                })

            location = results[0]
            latitude = location["latitude"]
            longitude = location["longitude"]

            # Retrieve current weather
            weather_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": (
                        "temperature_2m,"
                        "relative_humidity_2m,"
                        "apparent_temperature,"
                        "is_day,"
                        "precipitation,"
                        "weather_code,"
                        "wind_speed_10m"
                    ),
                    "timezone": "auto",
                },
            )

            weather_response.raise_for_status()
            weather_data = weather_response.json()

            result = {
                "city": location.get("name"),
                "country": location.get("country"),
                "latitude": latitude,
                "longitude": longitude,
                "current_weather": weather_data.get("current"),
                "units": weather_data.get("current_units"),
            }

            return json.dumps(result, indent=2)
    except httpx.TimeoutException:
        logging.exception("Weather API request timed out")
        return json.dumps({
            "error": "Weather API request timed out. Please try again."
        })

    except httpx.HTTPStatusError as error:
        logging.exception("Weather API returned an HTTP error")
        logging.error("Response body: %s", error.response.text)
        logging.error("Response headers: %s", dict(error.response.headers))

        return json.dumps({
            "error": f"Weather API error: {error.response.status_code}",
            "details": error.response.text
        })

    except httpx.RequestError:
        logging.exception("Could not connect to weather API")
        return json.dumps({
            "error": "Could not connect to Open-Meteo. Check your internet."
        })

    except (KeyError, ValueError):
        logging.exception("Unexpected weather API response")
        return json.dumps({
            "error": "Received an unexpected response from Open-Meteo."
        })


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
