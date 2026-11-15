# -*- coding: utf-8 -*-
"""Write a minimal star schema as CSV.

Facts store the cleaned measurement and, when that cell is blank, why.
Spike tests, AQI above 500, calendar parts, and daily rebuilds are left
for query time.
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data"
CLEAN = ROOT / "cleaned files"
OUT = ROOT / "model" / "csv"

POLLUTANTS = [
    ("pm25", "PM2.5"),
    ("pm10", "PM10"),
    ("no", "NO"),
    ("no2", "NO2"),
    ("nox", "NOx"),
    ("nh3", "NH3"),
    ("co", "CO"),
    ("so2", "SO2"),
    ("o3", "O3"),
    ("benzene", "Benzene"),
    ("toluene", "Toluene"),
    ("xylene", "Xylene"),
    ("aqi", "AQI"),
]
CODES = [code for code, _ in POLLUTANTS]
SOURCE = {code: source for code, source in POLLUTANTS}
BTX = ("benzene", "toluene", "xylene")


def num(value):
    value = (value or "").strip()
    if value == "":
        return None
    return float(value)


def text(value):
    return (value or "").strip()


def parse_time(value):
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(value)


def reason_for(code, raw_values, clean_value, series_has_value, co_copy):
    if clean_value is not None:
        return ""
    raw_value = raw_values[code]
    if raw_value is None:
        if not series_has_value[code]:
            return "not_recorded"
        return "original_null"
    if code == "co" and co_copy:
        return "co_copy_removed"
    pm25 = raw_values["pm25"]
    pm10 = raw_values["pm10"]
    removed_pair = (
        pm25 is not None and pm10 is not None
        and pm25 > pm10 * 1.5 and (pm25 - pm10) > 20
    )
    if code in ("pm25", "pm10") and removed_pair:
        return "pm_pair_removed"
    btx_zero = all(raw_values[name] == 0 for name in BTX)
    if code in BTX and btx_zero:
        return "btx_zero_removed"
    if code == "co" and btx_zero and raw_value == 0:
        return "co_zero_with_btx_removed"
    if code == "nox" and btx_zero and raw_value == 0:
        return "nox_zero_with_btx_removed"
    return "unexpected"


def iter_entities(path, entity_col, time_col):
    current = None
    bucket = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            entity = row[entity_col].strip()
            if entity != current:
                if entity in seen:
                    raise SystemExit(f"{path.name} is not grouped by {entity_col}")
                if bucket:
                    yield current, bucket
                seen.add(entity)
                current = entity
                bucket = []
            timestamp = parse_time(row[time_col])
            texts = {code: text(row[SOURCE[code]]) for code in CODES}
            values = {code: num(texts[code]) for code in CODES}
            bucket.append((timestamp, texts, values, text(row["AQI_Bucket"])))
        if bucket:
            yield current, bucket


def paired_entities(raw_path, clean_path, entity_col, time_col):
    raw_groups = iter_entities(raw_path, entity_col, time_col)
    clean_groups = iter_entities(clean_path, entity_col, time_col)
    while True:
        raw_item = next(raw_groups, None)
        clean_item = next(clean_groups, None)
        if raw_item is None and clean_item is None:
            break
        if raw_item is None or clean_item is None or raw_item[0] != clean_item[0]:
            raise SystemExit(f"Entity mismatch in {raw_path.name}")
        entity, raw_rows = raw_item
        _, clean_rows = clean_item
        if len(raw_rows) != len(clean_rows):
            raise SystemExit(f"{entity} row count differs between raw and cleaned")
        yield entity, raw_rows, clean_rows


def open_writer(path, header):
    handle = path.open("w", encoding="utf-8", newline="")
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(header)
    return handle, writer


def fact_header(entity_name, time_name, with_hour):
    header = [entity_name, time_name]
    if with_hour:
        header.append("hour")
    header.append("aqi_bucket")
    for code in CODES:
        header.extend([code, f"{code}_null_reason"])
    return header


def write_facts(raw_name, clean_name, entity_col, header_name, time_col, out_name, co_copy_ids, with_hour):
    print(f"writing {out_name}", flush=True)
    handle, writer = open_writer(
        OUT / out_name,
        fact_header(header_name, "full_date", with_hour),
    )
    rows = 0
    unexpected = 0
    removed_pairs = 0
    btx_rows = 0
    co_copy = 0
    for entity, raw_rows, clean_rows in paired_entities(
        RAW / raw_name, CLEAN / clean_name, entity_col, time_col
    ):
        series_has = {code: False for code in CODES}
        for _, _, values, _ in clean_rows:
            for code in CODES:
                if values[code] is not None:
                    series_has[code] = True
        co_copy_entity = entity in co_copy_ids
        for raw_row, clean_row in zip(raw_rows, clean_rows):
            timestamp, _, raw_values, _ = raw_row
            clean_time, clean_text, clean_values, bucket = clean_row
            if timestamp != clean_time:
                raise SystemExit(f"Timestamp mismatch for {entity}")
            record = [entity, timestamp.strftime("%Y-%m-%d")]
            if with_hour:
                record.append(timestamp.hour)
            record.append(bucket)
            pm25 = raw_values["pm25"]
            pm10 = raw_values["pm10"]
            if pm25 is not None and pm10 is not None and pm25 > pm10 * 1.5 and (pm25 - pm10) > 20:
                removed_pairs += 1
            if all(raw_values[name] == 0 for name in BTX):
                btx_rows += 1
            for code in CODES:
                reason = reason_for(code, raw_values, clean_values[code], series_has, co_copy_entity)
                if reason == "unexpected":
                    unexpected += 1
                if reason == "co_copy_removed":
                    co_copy += 1
                record.extend([clean_text[code], reason])
            writer.writerow(record)
            rows += 1
    handle.close()
    print(
        f"  rows {rows} unexpected {unexpected} removed_pairs {removed_pairs} "
        f"btx_rows {btx_rows} co_copy {co_copy}",
        flush=True,
    )
    return {
        "rows": rows,
        "unexpected": unexpected,
        "removed_pairs": removed_pairs,
        "btx_rows": btx_rows,
        "co_copy": co_copy,
    }


def write_dims():
    handle, writer = open_writer(
        OUT / "dim_date.csv",
        ["full_date", "year", "month", "day_of_month", "weekday"],
    )
    cursor = datetime(2015, 1, 1)
    end = datetime(2020, 7, 1)
    while cursor <= end:
        writer.writerow([
            cursor.strftime("%Y-%m-%d"),
            cursor.year,
            cursor.month,
            cursor.day,
            cursor.weekday() + 1,
        ])
        cursor += timedelta(days=1)
    handle.close()

    cities = {}
    stations = []
    with (CLEAN / "stations.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            city = row["City"].strip()
            state = row["State"].strip()
            if city in cities and cities[city] != state:
                raise SystemExit(f"{city} belongs to more than one state")
            cities[city] = state
            stations.append(row)
    handle, writer = open_writer(OUT / "dim_city.csv", ["city_name", "state"])
    for city in sorted(cities):
        writer.writerow([city, cities[city]])
    handle.close()

    handle, writer = open_writer(
        OUT / "dim_station.csv",
        ["station_id", "station_name", "city_name", "status"],
    )
    for row in sorted(stations, key=lambda item: item["StationId"]):
        writer.writerow([
            row["StationId"].strip(),
            row["StationName"].strip(),
            row["City"].strip(),
            row["Status"].strip(),
        ])
    handle.close()
    return len(cities), len(stations)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in ("dim_hour.csv", "dim_pollutant.csv", "rule_spike.csv"):
        path = OUT / stale
        if path.exists():
            path.unlink()
    city_count, station_count = write_dims()
    print(f"dims cities {city_count} stations {station_count}", flush=True)
    results = {
        "city_hour": write_facts(
            "city_hour.csv", "city_hour.csv", "City", "city_name", "Datetime",
            "fact_city_hour.csv", {"Ahmedabad"}, True,
        ),
        "station_hour": write_facts(
            "station_hour.csv", "station_hour.csv", "StationId", "station_id", "Datetime",
            "fact_station_hour.csv", {"GJ001"}, True,
        ),
        "city_day": write_facts(
            "city_day.csv", "city_day.csv", "City", "city_name", "Date",
            "fact_city_day.csv", {"Ahmedabad"}, False,
        ),
        "station_day": write_facts(
            "station_day.csv", "station_day.csv", "StationId", "station_id", "Date",
            "fact_station_day.csv", {"GJ001"}, False,
        ),
    }
    expected = {
        "city_hour_rows": (results["city_hour"]["rows"], 707875),
        "station_hour_rows": (results["station_hour"]["rows"], 2589083),
        "city_day_rows": (results["city_day"]["rows"], 29531),
        "station_day_rows": (results["station_day"]["rows"], 108035),
        "city_hour_unexpected": (results["city_hour"]["unexpected"], 0),
        "station_hour_unexpected": (results["station_hour"]["unexpected"], 0),
        "city_day_unexpected": (results["city_day"]["unexpected"], 0),
        "station_day_unexpected": (results["station_day"]["unexpected"], 0),
        "city_hour_pm_removed": (results["city_hour"]["removed_pairs"], 2265),
        "station_hour_pm_removed": (results["station_hour"]["removed_pairs"], 2273),
        "city_day_pm_removed": (results["city_day"]["removed_pairs"], 91),
        "station_day_pm_removed": (results["station_day"]["removed_pairs"], 96),
        "city_hour_btx": (results["city_hour"]["btx_rows"], 35971),
        "station_hour_btx": (results["station_hour"]["btx_rows"], 132861),
        "city_day_btx": (results["city_day"]["btx_rows"], 1494),
        "station_day_btx": (results["station_day"]["btx_rows"], 4890),
        "city_hour_co_copy": (results["city_hour"]["co_copy"], 31562),
        "station_hour_co_copy": (results["station_hour"]["co_copy"], 31562),
        "city_day_co_copy": (results["city_day"]["co_copy"], 1400),
        "station_day_co_copy": (results["station_day"]["co_copy"], 1400),
        "cities": (city_count, 127),
        "stations": (station_count, 230),
    }
    failed = False
    for name, (actual, target) in expected.items():
        ok = actual == target
        failed = failed or not ok
        print(("OK" if ok else "FAIL"), name, actual, target, flush=True)
    if failed:
        raise SystemExit("validation failed")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
