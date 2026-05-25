# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/weather.py

Weather lookup tool.
"""

import logging

import httpx

from lib.toolkit import tool

logger = logging.getLogger(__name__)

# wttr.in 免费 API
WEATHER_API = "https://wttr.in/{city}?format=%C+%t+%h+%w"


@tool()
async def get_weather(city: str) -> dict:
    """查询指定城市的当前天气。"""
    url = WEATHER_API.format(city=city)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            text = resp.text.strip()
            if not text or "Unknown" in text:
                return {"error": f"未找到城市 '{city}' 的天气信息"}
            return {"city": city, "weather": text}
    except Exception as e:
        logger.warning("天气查询失败 %s: %s", city, e)
        return {"error": f"天气查询失败: {str(e)}"}
