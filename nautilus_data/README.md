# Nautilus data directory

Mount point for Real Data backtests (`/nautilus_data` inside the backend container).

## Seed a minimal demo catalog

On the server (Docker Compose):

```bash
cd /root/nautilus-web
mkdir -p nautilus_data
docker compose run --rm -e CATALOG_PATH=/nautilus_data/catalog backend python3 scripts/seed_demo_catalog.py
docker compose up -d backend
```

Then in the Admin panel click **初始化引擎**, and run Real Data backtest with dates inside:

- Start: `2024-01-02`
- End: `2024-01-12`

(Do not use the full year 2024-01-01..2024-12-31 unless you seed that range.)

Generated files under `nautilus_data/catalog/` are gitignored.
