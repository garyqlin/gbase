# SPDX-License-Identifier: MIT
"""
gbase/tools/weather.py

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
    # Sanitize city input to prevent injection into URL
    import re

    safe_city = re.sub(r"[^a-zA-Z\u4e00-\u9fff\s,.-]", "", city.strip())[:100]
    if not safe_city:
        return {"error": "Invalid city name"}
    url = WEATHER_API.format(city=safe_city)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            text = resp.text.strip()
            if not text or "Unknown" in text:
                return {"error": f"City '{city}' not found"}
            return {"city": safe_city, "weather": text}
    except Exception as e:
        logger.warning("Weather query failed %s: %s", safe_city, e)
        return {"error": f"Weather query failed: {str(e)}"}
