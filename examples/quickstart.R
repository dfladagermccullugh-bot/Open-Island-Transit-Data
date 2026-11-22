# Read the daily figures and one day's stop events in R.
# Needs: install.packages(c("readr", "arrow"))
library(readr)
library(arrow)

base <- "https://raw.githubusercontent.com/dfladagermccullugh-bot/Open-Island-Transit-Data/data"

# One row per service day. Shares are fractions from 0 to 1.
quality <- read_csv(paste0(base, "/quality/daily.csv"))
ontime <- read_csv(paste0(base, "/ontime/daily.csv"))
print(tail(ontime[, c("service_date", "observed_share", "on_time_share_of_observed")]))

# files.csv lists every larger file with its address and SHA-256.
files <- read_csv(paste0(base, "/files.csv"))
latest <- tail(files[files$table == "stop-events", ], 1)
download.file(latest$url, latest$name, mode = "wb")

# One row per scheduled stop of every trip that day. Times are in UTC.
events <- read_parquet(latest$name)
print(table(events$departure_status[events$is_timepoint]))
