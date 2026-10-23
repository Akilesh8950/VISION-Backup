from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from typing import Any

import httpx

from . import schemas

USER_AGENT = "VISION Desktop (local tool runtime)"
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "ADA": "cardano", "USDT": "tether", "USDC": "usd-coin",
    "BNB": "binancecoin", "MATIC": "matic-network", "POL": "polygon-ecosystem-token",
}
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 61: "Slight rain", 63: "Rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Snow", 75: "Heavy snow", 80: "Slight rain showers",
    81: "Rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
}


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0), headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _ddgs(method: str, query: str, max_results: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS  # type: ignore
    except Exception as exc:
        raise RuntimeError("Web search dependency ddgs is not installed") from exc
    client = DDGS()
    function = getattr(client, method)
    try:
        result = function(query, max_results=max_results)
    except TypeError:
        result = function(query=query, max_results=max_results)
    return list(result or [])


def web_search(args: schemas.WebSearchArgs) -> dict[str, Any]:
    results = []
    for item in _ddgs("text", args.query, args.max_results):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("href") or item.get("url", ""),
            "snippet": item.get("body") or item.get("snippet", ""),
        })
    return {"query": args.query, "results": results}


def get_news(args: schemas.GetNewsArgs) -> dict[str, Any]:
    results = []
    for item in _ddgs("news", args.topic, args.limit):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url") or item.get("href", ""),
            "source": item.get("source", ""),
            "date": item.get("date", ""),
            "snippet": item.get("body") or item.get("snippet", ""),
        })
    return {"topic": args.topic, "results": results}


def get_weather(args: schemas.GetWeatherArgs) -> dict[str, Any]:
    with _client() as client:
        geo = client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": args.city, "count": 1, "language": "en", "format": "json"},
        )
        geo.raise_for_status()
        places = (geo.json().get("results") or [])
        if not places:
            raise RuntimeError(f"City not found: {args.city}")
        place = places[0]
        temperature_unit = "fahrenheit" if args.units == "imperial" else "celsius"
        wind_unit = "mph" if args.units == "imperial" else "kmh"
        forecast = client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 1,
                "temperature_unit": temperature_unit,
                "wind_speed_unit": wind_unit,
                "timezone": "auto",
            },
        )
        forecast.raise_for_status()
        data = forecast.json()
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    temp_symbol = "°F" if args.units == "imperial" else "°C"
    wind_symbol = "mph" if args.units == "imperial" else "km/h"

    def first(values):
        return values[0] if isinstance(values, list) and values else None

    return {
        "city": place.get("name", args.city),
        "country": place.get("country", ""),
        "temperature": current.get("temperature_2m"),
        "temperature_unit": temp_symbol,
        "apparent_temperature": current.get("apparent_temperature"),
        "condition": WEATHER_CODES.get(current.get("weather_code"), f"Weather code {current.get('weather_code')}") ,
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_unit": wind_symbol,
        "today_high": first(daily.get("temperature_2m_max")),
        "today_low": first(daily.get("temperature_2m_min")),
        "precipitation_probability_percent": first(daily.get("precipitation_probability_max")),
        "observed_at": current.get("time"),
        "source": "Open-Meteo",
    }


def parse_yahoo_chart(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(str(chart["error"]))
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No quote found for ticker {ticker}")
    result = results[0] or {}
    meta = result.get("meta") or {}
    quote_data = (((result.get("indicators") or {}).get("quote") or [{}])[0] or {})

    def last_number(key: str):
        values = quote_data.get(key) or []
        for value in reversed(values):
            if value is not None:
                return float(value)
        return None

    timestamp = meta.get("regularMarketTime")
    return {
        "ticker": str(meta.get("symbol") or ticker.upper()),
        "name": meta.get("longName") or meta.get("shortName") or "",
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "price": meta.get("regularMarketPrice"),
        "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "open": last_number("open"),
        "high": meta.get("regularMarketDayHigh") or last_number("high"),
        "low": meta.get("regularMarketDayLow") or last_number("low"),
        "close": meta.get("regularMarketPrice") or last_number("close"),
        "volume": meta.get("regularMarketVolume") or last_number("volume"),
        "observed_at": datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat() if timestamp else None,
        "source": "Yahoo Finance chart API",
    }


def get_stock_price(args: schemas.GetStockPriceArgs) -> dict[str, Any]:
    symbol = quote(args.ticker.strip().upper(), safe=".-^=")
    last_error: Exception | None = None
    with _client() as client:
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                response = client.get(
                    f"https://{host}/v8/finance/chart/{symbol}",
                    params={"range": "5d", "interval": "1d"},
                )
                response.raise_for_status()
                return parse_yahoo_chart(response.json(), args.ticker)
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"No quote found for ticker {args.ticker}: {last_error}")


def _coingecko_id(symbol: str, client: httpx.Client) -> str:
    clean = symbol.strip()
    if clean.upper() in COINGECKO_IDS:
        return COINGECKO_IDS[clean.upper()]
    if "-" in clean or clean.islower():
        return clean.lower()
    response = client.get("https://api.coingecko.com/api/v3/search", params={"query": clean})
    response.raise_for_status()
    coins = response.json().get("coins") or []
    for coin in coins:
        if str(coin.get("symbol", "")).upper() == clean.upper():
            return str(coin["id"])
    if coins:
        return str(coins[0]["id"])
    raise RuntimeError(f"Unknown cryptocurrency symbol {symbol}")


def get_crypto_price(args: schemas.GetCryptoPriceArgs) -> dict[str, Any]:
    currency = args.currency.lower()
    with _client() as client:
        coin_id = _coingecko_id(args.symbol, client)
        response = client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": currency, "include_24hr_change": "true", "include_last_updated_at": "true"},
        )
        if response.status_code in (401, 403, 429):
            raise RuntimeError(f"CoinGecko request was rejected or rate-limited (HTTP {response.status_code})")
        response.raise_for_status()
        data = response.json().get(coin_id) or {}
    if currency not in data:
        raise RuntimeError(f"No {args.currency.upper()} price returned for {args.symbol}")
    return {
        "symbol": args.symbol.upper(),
        "coin_id": coin_id,
        "currency": args.currency.upper(),
        "price": data[currency],
        "change_24h_percent": data.get(f"{currency}_24h_change"),
        "last_updated_at": data.get("last_updated_at"),
        "source": "CoinGecko",
    }


def lookup_docs(args: schemas.LookupDocsArgs) -> dict[str, Any]:
    query = f"{args.library} documentation {args.topic}"
    results = []
    for item in _ddgs("text", query, 5):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("href") or item.get("url", ""),
            "snippet": item.get("body") or item.get("snippet", ""),
        })
    return {"library": args.library, "topic": args.topic, "results": results}
