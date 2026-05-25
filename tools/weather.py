# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/weather.py

Weather lookup tool.
"""

import logging

import httpx

from lib.toolkit import tool

logger = logging.getLogger(__name__)

# wttr.in free weather API
WEATHER_API = "https://wttr.in/{city}?format=%C+%t+%h+%w"


@tool()
async def get_weather(city: str) -> dict:
    """Query current weather for a given city."""
    url = WEATHER_API.format(city=city)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            text = resp.text.strip()
            if not text or "Unknown" in text:
                return {"error": f"City '{city}' not found"}
            return {"city": city, "weather": text}
    except Exception as e:
        logger.warning("Weather query failed %s: %s", city, e)
        return {"error": f"Weather query failed: {str(e)}"}
