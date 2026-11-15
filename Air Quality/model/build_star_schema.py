# -*- coding: utf-8 -*-
"""Build the air-quality star schema from the cleaned CSV files.

Hour and day facts keep the stored measurement. Each pollutant also carries a
completeness flag and a spike flag. Daily facts add an analysis value: the mean
of that day's hours after isolated spikes and ceiling hits are left out. AQI is
stored as published and is not rebuilt from hours.
"""
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "cleaned files"
MODEL = ROOT / "model"
DB_PATH = MODEL / "air_quality.db"
SCHEMA_PATH = MODEL / "schema.sql"

# code, source column, display name, unit, absolute floor, min ratio,
# local floor, ceiling or None, rebuild daily mean from clean hours
POLLUTANTS = [
    ("pm25", "PM2.5", "PM2.5", "µg/m³", 80.0, 2.5, 30.0, 900.0, 1),
    ("pm10", "PM10", "PM10", "µg/m³", 120.0, 2.5, 40.0, 990.0, 1),
    ("no", "NO", "NO", "µg/m³", 40.0, 3.0, 15.0, 490.0, 1),
    ("no2", "NO2", "NO2", "µg/m³", 40.0, 3.0, 15.0, 490.0, 1),
    ("nox", "NOx", "NOx", "µg/m³", 50.0, 3.0, 20.0, 490.0, 1),
    ("nh3", "NH3", "NH3", "µg/m³", 40.0, 3.0, 15.0, 490.0, 1),
    ("co", "CO", "CO", "mg/m³", 2.0, 3.0, 1.0, 490.0, 1),
    ("so2", "SO2", "SO2", "µg/m³", 25.0, 3.0, 8.0, 190.0, 1),
    ("o3", "O3", "O3", "µg/m³", 50.0, 2.5, 20.0, None, 1),
    ("benzene", "Benzene", "Benzene", "µg/m³", 10.0, 5.0, 5.0, 490.0, 1),
    ("toluene", "Toluene", "Toluene", "µg/m³", 20.0, 5.0, 8.0, 490.0, 1),
    ("xylene", "Xylene", "Xylene", "µg/m³", 20.0, 5.0, 8.0, 490.0, 1),
    ("aqi", "AQI", "AQI", "index", 100.0, 2.0, 40.0, None, 0),
]
CODES = [p[0] for p in POLLUTANTS]
SOURCE = {p[0]: p[1] for p in POLLUTANTS}
RULE = {p[0]: (p[4], p[5], p[6], p[7]) for p in POLLUTANTS}
ROLLUP = {p[0]: p[8] for p in POLLUTANTS}
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def num(value):
    value = (value or "").strip()
    if value == "":
        return None
    return float(value)


def parse_time(value):
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(value)


def date_key(dt):
    return dt.year * 10000 + dt.month * 100 + dt.day


def agency_of(name):
    if " - " in name:
        return name.rsplit(" - ", 1)[1].strip() or None
    return None


def blank_to_none(value):
    value = (value or "").strip()
    return value or None


def _classify_streaming(rows, step, history_required, alpha):
    flags = []
    for _, values in rows:
        flags.append({
            code: "not_applicable" if values[code] is None else "ok"
            for code in CODES
        })
    ema = {code: [0.0, 0] for code in CODES}
    window = []
    for index, (timestamp, values) in enumerate(rows):
        if window and (timestamp - window[-1][0]) != step:
            window.clear()
            ema = {code: [0.0, 0] for code in CODES}
        window.append((timestamp, values, index))
        if len(window) > 3:
            window.pop(0)
        if len(window) < 3:
            continue
        (t0, v0, _), (t1, v1, i1), (t2, v2, _) = window
        if (t1 - t0) != step or (t2 - t1) != step:
            continue
        for code in CODES:
            left, mid, right = v0[code], v1[code], v2[code]
            if left is None or mid is None or right is None:
                continue
            if mid > left and mid > right:
                base = left if left >= right else right
                excess = mid - base
                abs_floor, ratio_min, local_floor, ceiling = RULE[code]
                global_hit = excess >= abs_floor and (base <= 0 or mid >= ratio_min * base)
                level, seen = ema[code]
                local_hit = False
                if seen >= history_required:
                    need = local_floor
                    if level > 0:
                        need = max(need, 8.0 * level)
                    local_hit = excess >= need and (base <= 0 or mid >= 3.0 * base)
                if global_hit and local_hit:
                    if ceiling is not None and mid >= ceiling:
                        flags[i1][code] = "ceiling"
                    else:
                        flags[i1][code] = "isolated_spike"
        for code in CODES:
            left, mid = v0[code], v1[code]
            if left is None or mid is None:
                continue
            level, seen = ema[code]
            jump = abs(mid - left)
            if seen == 0:
                ema[code][0] = jump
                ema[code][1] = 1
            else:
                ema[code][0] = (1.0 - alpha) * level + alpha * jump
                ema[code][1] = seen + 1
    return flags


def completeness_for(rows, null_label):
    present = {code: False for code in CODES}
    for _, values in rows:
        for code in CODES:
            if values[code] is not None:
                present[code] = True
    out = []
    for _, values in rows:
        flags = {}
        for code in CODES:
            if not present[code]:
                flags[code] = "not_recorded"
            elif values[code] is None:
                flags[code] = null_label
            else:
                flags[code] = "measured"
        out.append(flags)
    return out


def measure_columns(null_label, with_analysis):
    parts = []
    checks = []
    for code, _, _, _, _, _, _, _, rollup in POLLUTANTS:
        parts.append(f"    {code} REAL")
        parts.append(f"    {code}_spike TEXT NOT NULL")
        parts.append(f"    {code}_completeness TEXT NOT NULL")
        checks.append(
            f"    CHECK ({code}_spike IN ('ok','isolated_spike','ceiling','not_applicable'))"
        )
        checks.append(
            f"    CHECK ({code}_completeness IN ('measured','not_recorded','{null_label}'))"
        )
        if with_analysis and rollup:
            parts.append(f"    {code}_analysis REAL")
            parts.append(f"    {code}_analysis_hours INTEGER")
    return parts, checks


def create_schema(con):
    hour_cols, hour_checks = measure_columns("null_on_hour", False)
    day_cols, day_checks = measure_columns("null_on_day", True)
    hour_body = ",\n".join(hour_cols + hour_checks)
    day_body = ",\n".join(day_cols + day_checks)
    analysis_exprs = []
    for code, _, _, _, _, _, _, _, rollup in POLLUTANTS:
        if rollup:
            analysis_exprs.append(
                f"    CASE WHEN {code}_spike IN ('isolated_spike','ceiling') THEN NULL ELSE {code} END AS {code}"
            )
        else:
            analysis_exprs.append(f"    {code}")
        analysis_exprs.append(f"    {code}_spike")
    analysis_sql = ",\n".join(analysis_exprs)
    completeness_sql = ",\n".join(
        f"    {code}_completeness AS {code}" for code in CODES
    )
    schema = f"""-- Air quality star schema
-- Hour facts are the measurement base. Day facts hold the published daily
-- values from the cleaned day files. City facts are not averages of stations.
-- A spike flag of isolated_spike or ceiling marks one reading that jumped
-- above both neighbors and then fell back. The stored value is kept.
-- On day facts, <pollutant>_analysis is the mean of that day's hours after
-- those flagged hours are left out. AQI is never rebuilt.

PRAGMA foreign_keys = ON;

CREATE TABLE dim_date (
    date_key INTEGER PRIMARY KEY,
    full_date TEXT NOT NULL UNIQUE,
    year INTEGER NOT NULL,
    quarter INTEGER NOT NULL,
    month INTEGER NOT NULL,
    month_name TEXT NOT NULL,
    year_month TEXT NOT NULL,
    day_of_month INTEGER NOT NULL,
    weekday INTEGER NOT NULL,
    weekday_name TEXT NOT NULL,
    is_weekend INTEGER NOT NULL,
    is_partial_year INTEGER NOT NULL
);

CREATE TABLE dim_hour (
    hour_key INTEGER PRIMARY KEY,
    hour_label TEXT NOT NULL,
    day_part TEXT NOT NULL
);

CREATE TABLE dim_city (
    city_key INTEGER PRIMARY KEY,
    city_name TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    has_city_series INTEGER NOT NULL,
    has_station_series INTEGER NOT NULL
);

CREATE TABLE dim_station (
    station_key INTEGER PRIMARY KEY,
    station_id TEXT NOT NULL UNIQUE,
    station_name TEXT NOT NULL,
    city_key INTEGER NOT NULL REFERENCES dim_city(city_key),
    state TEXT NOT NULL,
    status TEXT,
    agency TEXT,
    has_measurements INTEGER NOT NULL
);

CREATE TABLE dim_pollutant (
    pollutant_key INTEGER PRIMARY KEY,
    column_name TEXT NOT NULL UNIQUE,
    pollutant_name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL,
    spike_abs_floor REAL NOT NULL,
    spike_ratio_min REAL NOT NULL,
    local_floor REAL NOT NULL,
    ceiling REAL,
    rebuild_daily_from_clean_hours INTEGER NOT NULL
);

CREATE TABLE rule_spike (
    grain TEXT PRIMARY KEY,
    history_required INTEGER NOT NULL,
    ewma_alpha REAL NOT NULL,
    scale_multiple REAL NOT NULL,
    neighbor_ratio REAL NOT NULL,
    rule_text TEXT NOT NULL
);

CREATE TABLE fact_city_hour (
    city_key INTEGER NOT NULL REFERENCES dim_city(city_key),
    date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    hour_key INTEGER NOT NULL REFERENCES dim_hour(hour_key),
    observed_at TEXT NOT NULL,
    aqi_bucket TEXT,
{hour_body},
    PRIMARY KEY (city_key, date_key, hour_key)
);

CREATE TABLE fact_station_hour (
    station_key INTEGER NOT NULL REFERENCES dim_station(station_key),
    date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    hour_key INTEGER NOT NULL REFERENCES dim_hour(hour_key),
    observed_at TEXT NOT NULL,
    aqi_bucket TEXT,
{hour_body},
    PRIMARY KEY (station_key, date_key, hour_key)
);

CREATE TABLE fact_city_day (
    city_key INTEGER NOT NULL REFERENCES dim_city(city_key),
    date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    observed_on TEXT NOT NULL,
    aqi_bucket TEXT,
{day_body},
    PRIMARY KEY (city_key, date_key)
);

CREATE TABLE fact_station_day (
    station_key INTEGER NOT NULL REFERENCES dim_station(station_key),
    date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    observed_on TEXT NOT NULL,
    aqi_bucket TEXT,
{day_body},
    PRIMARY KEY (station_key, date_key)
);

CREATE INDEX idx_city_hour_date ON fact_city_hour(date_key);
CREATE INDEX idx_station_hour_date ON fact_station_hour(date_key);
CREATE INDEX idx_city_day_date ON fact_city_day(date_key);
CREATE INDEX idx_station_day_date ON fact_station_day(date_key);
CREATE INDEX idx_station_city ON dim_station(city_key);

CREATE VIEW vw_fact_city_hour_analysis AS
SELECT
    city_key,
    date_key,
    hour_key,
    observed_at,
{analysis_sql},
    aqi_bucket
FROM fact_city_hour;

CREATE VIEW vw_fact_station_hour_analysis AS
SELECT
    station_key,
    date_key,
    hour_key,
    observed_at,
{analysis_sql},
    aqi_bucket
FROM fact_station_hour;

CREATE VIEW vw_mart_city_day_completeness AS
SELECT
    city_key,
    date_key,
    observed_on,
{completeness_sql}
FROM fact_city_day;

CREATE VIEW vw_mart_station_day_completeness AS
SELECT
    station_key,
    date_key,
    observed_on,
{completeness_sql}
FROM fact_station_day;
"""
    con.executescript(schema)
    SCHEMA_PATH.write_text(schema, encoding="utf-8")


def load_dimensions(con):
    start = datetime(2015, 1, 1)
    end = datetime(2020, 7, 1)
    dates = []
    cursor = start
    while cursor <= end:
        dates.append((
            date_key(cursor),
            cursor.strftime("%Y-%m-%d"),
            cursor.year,
            (cursor.month - 1) // 3 + 1,
            cursor.month,
            MONTHS[cursor.month - 1],
            cursor.strftime("%Y-%m"),
            cursor.day,
            cursor.weekday(),
            WEEKDAYS[cursor.weekday()],
            1 if cursor.weekday() >= 5 else 0,
            1 if cursor.year == 2020 else 0,
        ))
        cursor += timedelta(days=1)
    con.executemany(
        "INSERT INTO dim_date VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        dates,
    )
    hours = []
    for hour in range(24):
        if hour <= 5:
            part = "Night"
        elif hour <= 11:
            part = "Morning"
        elif hour <= 17:
            part = "Afternoon"
        else:
            part = "Evening"
        hours.append((hour, f"{hour:02d}:00", part))
    con.executemany("INSERT INTO dim_hour VALUES (?,?,?)", hours)
    con.executemany(
        "INSERT INTO dim_pollutant VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (i + 1, code, name, unit, abs_floor, ratio_min, local_floor, ceiling, rollup)
            for i, (code, _, name, unit, abs_floor, ratio_min, local_floor, ceiling, rollup)
            in enumerate(POLLUTANTS)
        ],
    )
    con.executemany(
        "INSERT INTO rule_spike VALUES (?,?,?,?,?,?)",
        [
            (
                "hour", 24, 0.05, 8.0, 3.0,
                "Isolated hour: above both neighboring hours by the pollutant floor and ratio, and at least 8 times the recent typical hour-to-hour change after 24 hours of history.",
            ),
            (
                "day", 10, 0.12, 8.0, 3.0,
                "Isolated day: above both neighboring days by the pollutant floor and ratio, and at least 8 times the recent typical day-to-day change after 10 days of history.",
            ),
        ],
    )

    city_states = defaultdict(set)
    station_rows = []
    with (CLEAN / "stations.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            city = row["City"].strip()
            state = row["State"].strip()
            city_states[city].add(state)
            station_rows.append(row)
    conflicts = {city: states for city, states in city_states.items() if len(states) > 1}
    if conflicts:
        raise SystemExit(f"City belongs to more than one state: {conflicts}")
    city_names = sorted(city_states)
    city_key = {name: i + 1 for i, name in enumerate(city_names)}
    con.executemany(
        "INSERT INTO dim_city VALUES (?,?,?,?,?)",
        [(city_key[name], name, next(iter(city_states[name])), 0, 0) for name in city_names],
    )
    station_rows.sort(key=lambda row: row["StationId"])
    station_key = {}
    payload = []
    for index, row in enumerate(station_rows, start=1):
        station_id = row["StationId"].strip()
        station_key[station_id] = index
        payload.append((
            index,
            station_id,
            row["StationName"].strip(),
            city_key[row["City"].strip()],
            row["State"].strip(),
            blank_to_none(row["Status"]),
            agency_of(row["StationName"]),
            0,
        ))
    con.executemany(
        "INSERT INTO dim_station VALUES (?,?,?,?,?,?,?,?)",
        payload,
    )
    return city_key, station_key


def iter_groups(path, entity_col, time_col):
    current = None
    bucket = []
    seen = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
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
            values = {code: num(row[SOURCE[code]]) for code in CODES}
            bucket.append((timestamp, values, blank_to_none(row["AQI_Bucket"])))
        if bucket:
            yield current, bucket


def hour_tuple(entity_key, timestamp, values, bucket, spikes, completeness):
    payload = [
        entity_key,
        date_key(timestamp),
        timestamp.hour,
        timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        bucket,
    ]
    for code in CODES:
        payload.extend([values[code], spikes[code], completeness[code]])
    return tuple(payload)


def accumulate(daily, entity, timestamp, values, spikes):
    key = (entity, date_key(timestamp))
    slot = daily.get(key)
    if slot is None:
        slot = {code: [0.0, 0] for code in CODES if ROLLUP[code]}
        daily[key] = slot
    for code in slot:
        if spikes[code] == "ok" and values[code] is not None:
            slot[code][0] += values[code]
            slot[code][1] += 1


def load_hours(con, path, entity_col, time_col, key_map, table, step, history, alpha):
    print(f"loading {path.name}", flush=True)
    groups = iter_groups(path, entity_col, time_col)
    daily = {}
    spike_count = 0
    row_count = 0
    placeholders = ",".join(["?"] * (5 + 3 * len(CODES)))
    sql = f"INSERT INTO {table} VALUES ({placeholders})"
    batch = []
    measured_entities = set()
    for entity, rows in groups:
        if entity not in key_map:
            raise SystemExit(f"{entity} from {path.name} is missing from the dimension")
        measured_entities.add(entity)
        plain = [(timestamp, values) for timestamp, values, _ in rows]
        spikes = _classify_streaming(plain, step, history, alpha)
        complete = completeness_for(plain, "null_on_hour")
        entity_key = key_map[entity]
        for (timestamp, values, bucket), spike_row, comp_row in zip(rows, spikes, complete):
            batch.append(hour_tuple(entity_key, timestamp, values, bucket, spike_row, comp_row))
            accumulate(daily, entity, timestamp, values, spike_row)
            spike_count += sum(flag in ("isolated_spike", "ceiling") for flag in spike_row.values())
            row_count += 1
            if len(batch) >= 20000:
                con.executemany(sql, batch)
                batch.clear()
    if batch:
        con.executemany(sql, batch)
    print(f"  rows {row_count} spikes {spike_count}", flush=True)
    return daily, spike_count, row_count, measured_entities


def day_tuple(entity_key, timestamp, values, bucket, spikes, completeness, analysis):
    payload = [
        entity_key,
        date_key(timestamp),
        timestamp.strftime("%Y-%m-%d"),
        bucket,
    ]
    for code, rollup in ((item[0], item[8]) for item in POLLUTANTS):
        payload.extend([values[code], spikes[code], completeness[code]])
        if rollup:
            if analysis is None:
                payload.extend([None, 0])
            else:
                total, count = analysis[code]
                if count:
                    payload.extend([round(total / count, 2), count])
                else:
                    payload.extend([None, 0])
    return tuple(payload)


def load_days(con, path, entity_col, time_col, key_map, table, daily, step, history, alpha):
    print(f"loading {path.name}", flush=True)
    groups = iter_groups(path, entity_col, time_col)
    spike_count = 0
    row_count = 0
    missing_hours = 0
    n_values = 4
    for _, _, _, _, _, _, _, _, rollup in POLLUTANTS:
        n_values += 3 + (2 if rollup else 0)
    sql = f"INSERT INTO {table} VALUES ({','.join(['?'] * n_values)})"
    batch = []
    for entity, rows in groups:
        plain = [(timestamp, values) for timestamp, values, _ in rows]
        spikes = _classify_streaming(plain, step, history, alpha)
        complete = completeness_for(plain, "null_on_day")
        entity_key = key_map[entity]
        for (timestamp, values, bucket), spike_row, comp_row in zip(rows, spikes, complete):
            key = (entity, date_key(timestamp))
            if key not in daily:
                missing_hours += 1
            batch.append(day_tuple(
                entity_key, timestamp, values, bucket, spike_row, comp_row, daily.get(key)
            ))
            spike_count += sum(flag in ("isolated_spike", "ceiling") for flag in spike_row.values())
            row_count += 1
            if len(batch) >= 10000:
                con.executemany(sql, batch)
                batch.clear()
    if batch:
        con.executemany(sql, batch)
    print(f"  rows {row_count} spikes {spike_count} days_without_hours {missing_hours}", flush=True)
    return spike_count, row_count, missing_hours


def mark_coverage(con, city_entities, station_entities, city_key, station_key):
    con.executemany(
        "UPDATE dim_city SET has_city_series = 1 WHERE city_key = ?",
        [(city_key[name],) for name in city_entities],
    )
    station_cities = set()
    con.executemany(
        "UPDATE dim_station SET has_measurements = 1 WHERE station_key = ?",
        [(station_key[station_id],) for station_id in station_entities],
    )
    rows = con.execute(
        "SELECT city_key FROM dim_station WHERE has_measurements = 1"
    ).fetchall()
    station_cities = {(row[0],) for row in rows}
    con.executemany(
        "UPDATE dim_city SET has_station_series = 1 WHERE city_key = ?",
        list(station_cities),
    )


def validate(con, counts):
    checks = []

    def expect(name, actual, expected):
        checks.append((name, actual, expected, actual == expected))

    expect("city_hour_rows", counts["city_hour_rows"], 707875)
    expect("station_hour_rows", counts["station_hour_rows"], 2589083)
    expect("city_day_rows", counts["city_day_rows"], 29531)
    expect("station_day_rows", counts["station_day_rows"], 108035)
    expect("city_hour_spikes", counts["city_hour_spikes"], 5395)
    expect("station_hour_spikes", counts["station_hour_spikes"], 10273)
    expect("city_day_spikes", counts["city_day_spikes"], 78)
    expect("station_day_spikes", counts["station_day_spikes"], 299)
    expect("city_days_without_hours", counts["city_missing"], 0)
    expect("station_days_without_hours", counts["station_missing"], 0)

    sample = con.execute(
        """
        SELECT h.so2, h.so2_spike, h.so2_completeness,
               d.so2, d.so2_spike, d.so2_completeness, d.so2_analysis, d.so2_analysis_hours,
               d.aqi
        FROM fact_city_hour h
        JOIN dim_city c ON c.city_key = h.city_key
        JOIN fact_city_day d ON d.city_key = h.city_key AND d.date_key = h.date_key
        WHERE c.city_name = 'Hyderabad'
          AND h.observed_at = '2017-06-21 12:00:00'
        """
    ).fetchone()
    print("hyderabad_2017-06-21_12", sample, flush=True)
    ok_sample = (
        sample is not None
        and sample[0] == 199.3
        and sample[1] == "ceiling"
        and sample[2] == "measured"
        and sample[3] == 11.12
        and sample[4] == "ok"
        and sample[5] == "measured"
        and sample[6] == 2.94
        and sample[7] == 23
        and sample[8] == 70.0
    )
    checks.append(("hyderabad_so2_example", sample, "199.3 ceiling / day 11.12 -> 2.94", ok_sample))

    ahmedabad = con.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN h.co IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN h.co_completeness = 'not_recorded' THEN 1 ELSE 0 END),
               SUM(CASE WHEN h.co_spike = 'not_applicable' THEN 1 ELSE 0 END)
        FROM fact_city_hour h
        JOIN dim_city c ON c.city_key = h.city_key
        WHERE c.city_name = 'Ahmedabad'
        """
    ).fetchone()
    print("ahmedabad_co", ahmedabad, flush=True)
    checks.append((
        "ahmedabad_co_not_recorded",
        ahmedabad,
        "all null and not_recorded",
        ahmedabad[0] > 0 and ahmedabad[0] == ahmedabad[1] == ahmedabad[2] == ahmedabad[3],
    ))

    gj = con.execute(
        """
        SELECT SUM(CASE WHEN co_completeness = 'not_recorded' THEN 1 ELSE 0 END), COUNT(*)
        FROM fact_station_hour h
        JOIN dim_station s ON s.station_key = h.station_key
        WHERE s.station_id = 'GJ001'
        """
    ).fetchone()
    print("gj001_co", gj, flush=True)
    checks.append(("gj001_co_not_recorded", gj, "all not_recorded", gj[0] == gj[1] and gj[1] > 0))

    cities = con.execute("SELECT COUNT(*) FROM dim_city").fetchone()[0]
    stations = con.execute("SELECT COUNT(*) FROM dim_station").fetchone()[0]
    city_series = con.execute("SELECT COUNT(*) FROM dim_city WHERE has_city_series = 1").fetchone()[0]
    station_series = con.execute("SELECT COUNT(*) FROM dim_station WHERE has_measurements = 1").fetchone()[0]
    print(f"dims cities {cities} city_series {city_series} stations {stations} with_facts {station_series}", flush=True)
    expect("cities", cities, 127)
    expect("city_series", city_series, 26)
    expect("stations", stations, 230)
    expect("stations_with_facts", station_series, 110)

    failed = [item for item in checks if not item[3]]
    for name, actual, expected, ok in checks:
        print(("OK" if ok else "FAIL"), name, "actual", actual, "expected", expected, flush=True)
    if failed:
        raise SystemExit(f"{len(failed)} validation checks failed")


def main():
    MODEL.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA temp_store = MEMORY")
    con.execute("PRAGMA foreign_keys = OFF")
    create_schema(con)
    city_key, station_key = load_dimensions(con)
    con.commit()
    city_daily, city_hour_spikes, city_hour_rows, city_entities = load_hours(
        con, CLEAN / "city_hour.csv", "City", "Datetime", city_key,
        "fact_city_hour", timedelta(hours=1), 24, 0.05,
    )
    station_daily, station_hour_spikes, station_hour_rows, station_entities = load_hours(
        con, CLEAN / "station_hour.csv", "StationId", "Datetime", station_key,
        "fact_station_hour", timedelta(hours=1), 24, 0.05,
    )
    city_day_spikes, city_day_rows, city_missing = load_days(
        con, CLEAN / "city_day.csv", "City", "Date", city_key,
        "fact_city_day", city_daily, timedelta(days=1), 10, 0.12,
    )
    station_day_spikes, station_day_rows, station_missing = load_days(
        con, CLEAN / "station_day.csv", "StationId", "Date", station_key,
        "fact_station_day", station_daily, timedelta(days=1), 10, 0.12,
    )
    mark_coverage(con, city_entities, station_entities, city_key, station_key)
    con.commit()
    con.execute("PRAGMA foreign_keys = ON")
    fk_issues = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk_issues:
        raise SystemExit(f"Foreign key check failed: {fk_issues[:5]}")
    validate(con, {
        "city_hour_rows": city_hour_rows,
        "station_hour_rows": station_hour_rows,
        "city_day_rows": city_day_rows,
        "station_day_rows": station_day_rows,
        "city_hour_spikes": city_hour_spikes,
        "station_hour_spikes": station_hour_spikes,
        "city_day_spikes": city_day_spikes,
        "station_day_spikes": station_day_spikes,
        "city_missing": city_missing,
        "station_missing": station_missing,
    })
    con.close()
    print("wrote", DB_PATH, flush=True)


if __name__ == "__main__":
    main()
