# Read the daily figures and one day's stop events with pandas.
# Needs: pip install pandas pyarrow
import urllib.request

import pandas as pd

BASE = "https://raw.githubusercontent.com/dfladagermccullugh-bot/Open-Island-Transit-Data/data"

# One row per service day. Shares are fractions from 0 to 1.
quality = pd.read_csv(f"{BASE}/quality/daily.csv", parse_dates=["service_date"])
ontime = pd.read_csv(f"{BASE}/ontime/daily.csv", parse_dates=["service_date"])
print(ontime[["service_date", "observed_share", "on_time_share_of_observed"]].tail())

# files.csv lists every larger file with its address and SHA-256.
files = pd.read_csv(f"{BASE}/files.csv")
latest = files[files["table"] == "stop-events"].iloc[-1]
urllib.request.urlretrieve(latest["url"], latest["name"])

# One row per scheduled stop of every trip that day. Times are in UTC.
events = pd.read_parquet(latest["name"])
timepoints = events[events["is_timepoint"]]
print(latest["service_date"], timepoints["departure_status"].value_counts().to_dict())
