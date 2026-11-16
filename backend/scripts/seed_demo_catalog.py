#!/usr/bin/env python3
"""
Seed a minimal Nautilus Parquet catalog for Real Data backtests.

Generates synthetic EUR/USD.SIM quote ticks (weekdays only) so Docker deploys
can initialize the engine without external market data downloads.

Usage (on host with repo layout):
  python3 backend/scripts/seed_demo_catalog.py

Usage (Docker Compose — preferred on the Aliyun ECS):
  docker compose run --rm backend python3 scripts/seed_demo_catalog.py

Default range: 2024-01-02 .. 2024-01-12 (UTC). Override with env:
  CATALOG_PATH=/nautilus_data/catalog
  SEED_START=2024-01-02
  SEED_END=2024-01-12
"""

from __future__ import annotations

import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `python3 scripts/seed_demo_catalog.py` from /app in the container
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _weekdays(start: datetime, end: datetime):
    day = start
    while day <= end:
        if day.weekday() < 5:  # Mon-Fri
            yield day
        day += timedelta(days=1)


def main() -> int:
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    catalog_path = Path(
        os.environ.get(
            "CATALOG_PATH",
            os.environ.get(
                "NAUTILUS_CATALOG_PATH",
                str(Path(__file__).resolve().parents[2] / "nautilus_data" / "catalog"),
            ),
        )
    )
    start = _parse_day(os.environ.get("SEED_START", "2024-01-02"))
    end = _parse_day(os.environ.get("SEED_END", "2024-01-12"))
    force = os.environ.get("SEED_FORCE", "1") not in ("0", "false", "False")

    print(f"Catalog path : {catalog_path}")
    print(f"Date range   : {start.date()} -> {end.date()} (weekdays)")

    if catalog_path.exists() and force:
        print(f"Removing existing catalog at {catalog_path}")
        shutil.rmtree(catalog_path)
    catalog_path.mkdir(parents=True, exist_ok=True)

    venue = Venue("SIM")
    try:
        instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD", venue=venue)
    except TypeError:
        instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")

    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_instruments([instrument])

    random.seed(42)
    mid = 1.10000
    half_spread = 0.00005
    size = Quantity.from_str("1000000")
    bar_ns = 60_000_000_000
    ticks: list[QuoteTick] = []

    for day in _weekdays(start, end):
        # ~24h of 1-minute bars as 4 OHLC quote ticks each
        day_start = int(day.timestamp() * 1_000_000_000)
        for i in range(24 * 60):
            change = random.gauss(0.00002, 0.00025)
            open_p = mid
            close_p = max(0.5, mid * (1 + change))
            high_p = max(open_p, close_p) * (1 + abs(random.gauss(0, 0.00005)))
            low_p = min(open_p, close_p) * (1 - abs(random.gauss(0, 0.00005)))
            base_ts = day_start + i * bar_ns
            for j, px in enumerate((open_p, high_p, low_p, close_p)):
                ts = base_ts + j * 1_000_000
                bid = max(0.5, px - half_spread)
                ask = px + half_spread
                ticks.append(
                    QuoteTick(
                        instrument_id=instrument.id,
                        bid_price=Price.from_str(f"{bid:.5f}"),
                        ask_price=Price.from_str(f"{ask:.5f}"),
                        bid_size=size,
                        ask_size=size,
                        ts_event=ts,
                        ts_init=ts,
                    )
                )
            mid = close_p

    print(f"Writing {len(ticks)} quote ticks for {instrument.id} …")
    catalog.write_quote_ticks(ticks)

    instruments = catalog.instruments()
    print(f"Done. instruments={len(instruments)} path={catalog_path}")
    print("In the UI: Initialize Engine, then Real Data backtest with dates inside this range.")
    print(f"Suggested dates: {start.date()} -> {end.date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
