-- Read the daily figures and one day's stop events with DuckDB.
-- Run with: duckdb < quickstart.sql
INSTALL httpfs;
LOAD httpfs;

-- One row per service day. Shares are fractions from 0 to 1.
SELECT service_date, trip_coverage, vehicle_uptime
FROM read_csv('https://raw.githubusercontent.com/dfladagermccullugh-bot/Open-Island-Transit-Data/data/quality/daily.csv')
ORDER BY service_date DESC
LIMIT 7;

-- files.csv lists every larger file with its address and SHA-256.
SELECT service_date, name, url
FROM read_csv('https://raw.githubusercontent.com/dfladagermccullugh-bot/Open-Island-Transit-Data/data/files.csv')
WHERE "table" = 'stop-events'
ORDER BY service_date DESC
LIMIT 1;

-- Download that file (curl -L -o <name> <url>), then put its name here. One row per
-- scheduled stop of every trip that day; times are in UTC.
-- SELECT route_id, departure_status, count(*) AS stops
-- FROM 'stop-events-2026-11-20.parquet'
-- WHERE is_timepoint
-- GROUP BY ALL
-- ORDER BY route_id, stops DESC;
