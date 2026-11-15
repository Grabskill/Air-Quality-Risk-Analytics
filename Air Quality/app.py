"""Urban Air Quality Intelligence: India 2015–2020.

Executive Streamlit app for the cleaned city daily and hourly files.
Run from this folder:

    streamlit run app.py

The headline numbers are the middle city in the selection, not a pool of
days. That keeps one monitor, especially Ahmedabad, from setting the card.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots

# Fair window used for cross-city comparison. 2020 ends 1 July.
WINDOW_START = pd.Timestamp("2019-01-01")
WINDOW_END = pd.Timestamp("2020-07-01")
POLLUTANTS = ["PM2.5", "PM10", "NO2", "SO2", "CO", "O3"]
POLLUTANT_NAMES = {
    "PM2.5": "Fine particles (PM2.5)",
    "PM10": "Coarse particles (PM10)",
    "NO2": "Nitrogen dioxide",
    "SO2": "Sulfur dioxide",
    "CO": "Carbon monoxide",
    "O3": "Ozone",
}
# A month or a clock hour needs this many readings before it is drawn.
MIN_MONTH_DAYS = 20
MIN_HOUR_READINGS = 30

ARCHETYPES = {
    "Northern megacities": ["Delhi", "Patna", "Lucknow", "Gurugram"],
    "Industrial and sulfur hubs": ["Ahmedabad", "Talcher", "Brajrajnagar", "Jorapokhar"],
    "Coal and mining belts": ["Talcher", "Brajrajnagar"],
    "Clean coastal and southern hubs": [
        "Bengaluru",
        "Chennai",
        "Mumbai",
        "Hyderabad",
        "Thiruvananthapuram",
    ],
}

# Restrained series colors. Northern red, industrial amber, southern blue,
# then a short list for extra cities.
CITY_COLORS = [
    "#E45756",
    "#F58518",
    "#4C78A8",
    "#54A24B",
    "#B279A2",
    "#9D755D",
    "#EECA3B",
    "#72B7B2",
    "#FF9DA6",
    "#BAB0AC",
]
WINTER_BAND = "rgba(228, 87, 86, 0.14)"
MONSOON_BAND = "rgba(46, 107, 69, 0.12)"
CLEAN_BAND = "rgba(46, 107, 69, 0.12)"
EVENING_BAND = "rgba(228, 87, 86, 0.14)"


def find_data(name: str) -> Path:
    """Locate a cleaned CSV next to this app. Stop with a clear message if it is missing."""
    root = Path(__file__).resolve().parent
    candidates = [
        root / "cleaned files" / name,
        root / "cleaned_files" / name,
        root / "data" / name,
    ]
    for path in candidates:
        if path.exists():
            return path
    searched = "\n".join(f"- {path}" for path in candidates)
    st.error(
        f"Could not find `{name}`.\n\n"
        "Put the cleaned city files next to app.py in one of these locations:\n"
        f"{searched}\n\n"
        "The app uses the cleaned files on purpose. The raw Ahmedabad carbon monoxide "
        "column is a copy of another gas and should not drive a chart."
    )
    st.stop()


@st.cache_data(show_spinner="Loading daily records…")
def load_day(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["Date"])
    needed = {"City", "Date", "AQI", *POLLUTANTS}
    missing = needed.difference(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
    return frame


@st.cache_data(show_spinner="Loading hourly records…")
def load_hour(path: str) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        usecols=["City", "Datetime", "PM2.5", "NO2"],
        parse_dates=["Datetime"],
    )
    frame["Hour"] = frame["Datetime"].dt.hour
    return frame


def apply_window(frame: pd.DataFrame, column: str, full_history: bool) -> pd.DataFrame:
    if full_history:
        return frame
    dates = frame[column]
    return frame.loc[(dates >= WINDOW_START) & (dates <= WINDOW_END)].copy()


def city_aqi_stats(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per city: typical day, bad day, and the share of days above 200."""
    rows = []
    for city, group in frame.groupby("City"):
        aqi = group["AQI"].dropna()
        particles = group["PM2.5"].dropna()
        if aqi.empty:
            continue
        rows.append(
            {
                "City": city,
                "days": int(aqi.shape[0]),
                "median": float(aqi.median()),
                "p90": float(aqi.quantile(0.9)),
                "share_above_200": float((aqi > 200).mean() * 100),
                "pm25_median": float(particles.median()) if not particles.empty else np.nan,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["City", "days", "median", "p90", "share_above_200", "pm25_median"])
    return pd.DataFrame(rows).sort_values("p90", ascending=False)


def pollutant_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """City median Z-score. The mean and spread come from the whole window, not from one city.

    A city-pollutant cell is left blank when that city has fewer than 30 measured
    days. Ahmedabad carbon monoxide is blank in the cleaned file, so it stays blank.
    """
    present = frame[POLLUTANTS]
    center = present.mean()
    spread = present.std(ddof=0).replace(0, np.nan)
    scored = (present - center) / spread
    scored["City"] = frame["City"].to_numpy()
    rows = []
    for city, group in scored.groupby("City"):
        source = frame.loc[frame["City"] == city, POLLUTANTS]
        row = {"City": city}
        for pollutant in POLLUTANTS:
            measured = int(source[pollutant].notna().sum())
            row[pollutant] = float(group[pollutant].median()) if measured >= 30 else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("City")


def dominant_pollutant(scores: pd.DataFrame) -> tuple[str, float] | None:
    """Pollutant with the highest middle-city score. A negative result means nothing is above the window."""
    if scores.empty:
        return None
    summary = scores.median(axis=0, skipna=True).dropna()
    if summary.empty:
        return None
    name = str(summary.idxmax())
    return name, float(summary.loc[name])


def monthly_medians(frame: pd.DataFrame) -> pd.DataFrame:
    """Median AQI by city, year, and month. Thin months are dropped."""
    measured = frame.loc[frame["AQI"].notna(), ["City", "Date", "AQI"]].copy()
    if measured.empty:
        return measured
    measured["Year"] = measured["Date"].dt.year
    measured["Month"] = measured["Date"].dt.month
    grouped = (
        measured.groupby(["City", "Year", "Month"], sort=True)["AQI"]
        .agg(median="median", days="count")
        .reset_index()
    )
    return grouped.loc[grouped["days"] >= MIN_MONTH_DAYS]


def hour_profile(frame: pd.DataFrame, pollutant: str) -> pd.DataFrame:
    """Median by city and clock hour. Hours with too few readings are dropped."""
    measured = frame.loc[frame[pollutant].notna(), ["City", "Hour", pollutant]]
    if measured.empty:
        return pd.DataFrame(columns=["City", "Hour", "median"])
    grouped = (
        measured.groupby(["City", "Hour"], sort=True)[pollutant]
        .agg(median="median", n="count")
        .reset_index()
    )
    return grouped.loc[grouped["n"] >= MIN_HOUR_READINGS, ["City", "Hour", "median"]]


def year_palette(years: list[int]) -> dict[int, str]:
    if len(years) == 1:
        return {years[0]: "#7B1E3A"}
    samples = sample_colorscale(
        "Reds",
        [0.35 + 0.65 * i / (len(years) - 1) for i in range(len(years))],
    )
    return dict(zip(years, samples))


def add_season_bands(fig: go.Figure, row: int | None = None, col: int | None = None) -> None:
    """Shade winter (Jan–Feb and December) and the monsoon (Jun–Sep)."""
    spans = [
        (0.5, 2.5, WINTER_BAND, "Winter"),
        (5.5, 9.5, MONSOON_BAND, "Monsoon"),
        (11.5, 12.5, WINTER_BAND, "Winter"),
    ]
    for x0, x1, color, label in spans:
        kwargs = {} if row is None else {"row": row, "col": col}
        fig.add_vrect(
            x0=x0,
            x1=x1,
            fillcolor=color,
            line_width=0,
            layer="below",
            annotation_text=label if row in (None, 1) else "",
            annotation_position="top left",
            **kwargs,
        )


def add_hour_bands(fig: go.Figure) -> None:
    """Shade the late-evening risk zone and the mid-afternoon clean window on both panels."""
    spans = [
        (13.5, 17.5, CLEAN_BAND, "Clean window"),
        (19.5, 24, EVENING_BAND, "Late evening"),
    ]
    for row in (1, 2):
        for x0, x1, color, label in spans:
            fig.add_vrect(
                x0=x0,
                x1=x1,
                fillcolor=color,
                line_width=0,
                layer="below",
                annotation_text=label if row == 1 else "",
                annotation_position="top left",
                row=row,
                col=1,
            )


def style_figure(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=16, r=16, t=48, b=16),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        font=dict(family="Segoe UI, sans-serif", size=13),
        hovermode="x unified",
    )
    return fig


def severity_chart(stats: pd.DataFrame) -> go.Figure:
    ordered = stats.sort_values("p90", ascending=True)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Median AQI",
            y=ordered["City"],
            x=ordered["median"],
            orientation="h",
            marker_color="#4C78A8",
            customdata=np.column_stack([ordered["pm25_median"], ordered["days"]]),
            hovertemplate="%{y}<br>Median AQI %{x:.0f}<br>Fine-particle median %{customdata[0]:.0f}<br>%{customdata[1]} days<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            name="90th percentile AQI",
            y=ordered["City"],
            x=ordered["p90"],
            orientation="h",
            marker_color="#E45756",
            hovertemplate="%{y}<br>90th percentile AQI %{x:.0f}<extra></extra>",
        )
    )
    fig.update_layout(barmode="group", xaxis_title="AQI", yaxis_title="")
    return style_figure(fig, height=max(360, 48 * len(ordered) + 120))


def heatmap_chart(scores: pd.DataFrame, aqi_order: list[str]) -> go.Figure:
    ordered = [city for city in aqi_order if city in scores.index]
    ordered += [city for city in scores.index if city not in ordered]
    matrix = scores.loc[ordered, POLLUTANTS]
    # Highest usual AQI at the top. imshow draws the first row at the bottom.
    matrix = matrix.iloc[::-1]
    values = matrix.to_numpy(dtype=float)
    labels = np.where(np.isnan(values), "", np.char.mod("%.2f", np.nan_to_num(values)))
    fig = go.Figure(
        data=go.Heatmap(
            z=values,
            x=[POLLUTANT_NAMES[name] for name in POLLUTANTS],
            y=matrix.index.tolist(),
            colorscale="RdBu_r",
            zmid=0,
            colorbar=dict(title="Median Z-score"),
            hovertemplate="%{y}<br>%{x}<br>Median Z-score %{z:.2f}<extra></extra>",
            text=labels,
            texttemplate="%{text}" if len(matrix) <= 12 else None,
        )
    )
    fig.update_layout(
        xaxis_title="Pollutant",
        yaxis_title="",
        plot_bgcolor="#E8E8E8",
    )
    return style_figure(fig, height=max(420, 36 * len(matrix) + 160))


def season_chart(monthly: pd.DataFrame, cities: list[str]) -> go.Figure:
    years = sorted(int(year) for year in monthly["Year"].unique())
    colors = year_palette(years)
    use_facets = 1 < len(cities) <= 6
    if use_facets:
        cols = 3 if len(cities) > 3 else len(cities)
        rows = int(np.ceil(len(cities) / cols))
        fig = make_subplots(
            rows=rows,
            cols=cols,
            subplot_titles=cities,
            shared_xaxes=False,
            vertical_spacing=0.12,
            horizontal_spacing=0.06,
        )
        for index, city in enumerate(cities):
            row = index // cols + 1
            col = index % cols + 1
            part = monthly.loc[monthly["City"] == city]
            for year in years:
                year_part = part.loc[part["Year"] == year].sort_values("Month")
                if year_part.empty:
                    continue
                fig.add_trace(
                    go.Scatter(
                        name=str(year),
                        x=year_part["Month"],
                        y=year_part["median"],
                        mode="lines+markers",
                        line=dict(color=colors[year], width=2),
                        marker=dict(size=5),
                        legendgroup=str(year),
                        showlegend=index == 0,
                        hovertemplate=f"{city} {year}<br>Month %{{x}}<br>Median AQI %{{y:.0f}}<extra></extra>",
                    ),
                    row=row,
                    col=col,
                )
            add_season_bands(fig, row=row, col=col)
            fig.update_xaxes(
                tickmode="array",
                tickvals=list(range(1, 13)),
                ticktext=["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"],
                row=row,
                col=col,
            )
            fig.update_yaxes(title_text="Median AQI" if col == 1 else "", row=row, col=col)
        return style_figure(fig, height=320 * rows)
    # One city, or too many cities for a readable grid: one line per year.
    # With many cities the point is the middle city, so a long record cannot dominate.
    fig = go.Figure()
    if len(cities) == 1:
        source = monthly
        title_city = cities[0]
    else:
        source = (
            monthly.groupby(["Year", "Month"], sort=True)["median"]
            .median()
            .reset_index()
        )
        title_city = "Middle city"
    for year in years:
        if len(cities) == 1:
            year_part = source.loc[source["Year"] == year].sort_values("Month")
        else:
            year_part = source.loc[source["Year"] == year].sort_values("Month")
        if year_part.empty:
            continue
        fig.add_trace(
            go.Scatter(
                name=str(year),
                x=year_part["Month"],
                y=year_part["median"],
                mode="lines+markers",
                line=dict(color=colors[year], width=2.4),
                marker=dict(size=6),
                hovertemplate=f"{title_city} {year}<br>Month %{{x}}<br>Median AQI %{{y:.0f}}<extra></extra>",
            )
        )
    add_season_bands(fig)
    fig.update_xaxes(
        title_text="Month",
        tickmode="array",
        tickvals=list(range(1, 13)),
        ticktext=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        range=[0.5, 12.5],
    )
    fig.update_yaxes(title_text="Median AQI")
    return style_figure(fig, height=460)


def chart_lines(cities: list[str], chosen_groups: list[str]) -> list[tuple[str, str, list[str]]]:
    """One line per city when the list is short. Otherwise one line per group.

    Talcher and Brajrajnagar sit in both the industrial group and the coal belt.
    If the industrial group is selected, they stay on that line.
    """
    if len(cities) <= 6:
        return [(city, CITY_COLORS[index % len(CITY_COLORS)], [city]) for index, city in enumerate(cities)]
    colors = {
        "Northern megacities": "#E45756",
        "Industrial and sulfur hubs": "#F58518",
        "Coal and mining belts": "#9D755D",
        "Clean coastal and southern hubs": "#4C78A8",
    }
    active = [name for name in ARCHETYPES if name in chosen_groups]
    if "Industrial and sulfur hubs" in active:
        active = [name for name in active if name != "Coal and mining belts"]
    claimed: set[str] = set()
    lines: list[tuple[str, str, list[str]]] = []
    for name in active:
        present = [city for city in ARCHETYPES[name] if city in cities and city not in claimed]
        if present:
            claimed.update(present)
            lines.append((name, colors[name], present))
    for index, city in enumerate(city for city in cities if city not in claimed):
        lines.append((city, CITY_COLORS[index % len(CITY_COLORS)], [city]))
    return lines


def middle_profile(hour: pd.DataFrame, cities: list[str], pollutant: str) -> pd.Series:
    """Median across cities at each clock hour, so one city cannot set the wave."""
    columns = []
    for city in cities:
        profile = hour_profile(hour.loc[hour["City"] == city], pollutant)
        if profile.empty:
            continue
        columns.append(profile.set_index("Hour")["median"].rename(city))
    if not columns:
        return pd.Series(dtype=float)
    return pd.concat(columns, axis=1).median(axis=1)


def hour_label(hour: int) -> str:
    if hour == 0:
        return "12 AM"
    if hour < 12:
        return f"{hour} AM"
    if hour == 12:
        return "12 PM"
    return f"{hour - 12} PM"


def clock_level(clock: pd.Series, hours: range, use_peak: bool) -> str:
    part = clock.reindex(list(hours)).dropna()
    if part.empty:
        return "—"
    if use_peak:
        moment = int(part.idxmax())
        return f"{hour_label(moment)} · {part.loc[moment]:.0f}"
    return f"{float(part.median()):.0f}"


def diurnal_chart(hour: pd.DataFrame, lines: list[tuple[str, str, list[str]]]) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        subplot_titles=("Fine particles, PM2.5", "Nitrogen dioxide"),
        vertical_spacing=0.12,
    )
    for name, color, members in lines:
        for row, pollutant in ((1, "PM2.5"), (2, "NO2")):
            profile = middle_profile(hour, members, pollutant)
            if profile.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    name=name,
                    x=profile.index,
                    y=profile.to_numpy(),
                    mode="lines+markers",
                    line=dict(color=color, width=2.4),
                    marker=dict(size=5),
                    legendgroup=name,
                    showlegend=row == 1,
                    hovertemplate=f"{name}<br>%{{x}}:00<br>{pollutant} %{{y:.1f}}<extra></extra>",
                ),
                row=row,
                col=1,
            )
    add_hour_bands(fig)
    fig.update_xaxes(
        title_text="Hour of day",
        tickmode="array",
        tickvals=list(range(0, 24, 2)),
        ticktext=[f"{hour:02d}:00" for hour in range(0, 24, 2)],
        range=[-0.5, 23.5],
        row=2,
        col=1,
    )
    fig.update_yaxes(title_text="PM2.5", row=1, col=1)
    fig.update_yaxes(title_text="NO2", row=2, col=1)
    return style_figure(fig, height=640)


def render_kpis(stats: pd.DataFrame, scores: pd.DataFrame, window_label: str) -> None:
    qualified = stats.loc[stats["days"] >= MIN_MONTH_DAYS]
    thin = qualified.empty
    if thin:
        qualified = stats
    day_rule = (
        "fewer than 20 AQI days, so the cards use the thin records that remain"
        if thin
        else f"at least {MIN_MONTH_DAYS} AQI days"
    )
    st.caption(
        f"{window_label}. Each card is the middle value across {len(qualified)} cities with {day_rule}. "
        "Ahmedabad’s AQI cannot set the typical-day card while other cities are selected. "
        "A full-history number is not a like-for-like rank, because cities start in different years."
    )
    median_aqi = float(qualified["median"].median())
    peak_aqi = float(qualified["p90"].median())
    share = float(qualified["share_above_200"].median())
    dominant = dominant_pollutant(scores.loc[scores.index.intersection(qualified["City"])])
    if dominant is None:
        dominant_label = "Not enough days"
        dominant_help = "None of the six pollutants has 30 measured days in this selection."
    else:
        name, score = dominant
        if score >= 0.30:
            dominant_label = POLLUTANT_NAMES[name]
            dominant_help = (
                f"{POLLUTANT_NAMES[name]} leads this selection, with a middle-city "
                f"Z-score of {score:+.2f} against the typical day in this window."
            )
        elif score > 0:
            dominant_label = "No clear lead"
            dominant_help = (
                f"The cities in this mix pull in different directions, so no pollutant stands clear. "
                f"The slight lead is {POLLUTANT_NAMES[name]} at {score:+.2f}. "
                "Select one group to see its fingerprint."
            )
        else:
            dominant_label = "None above the window"
            dominant_help = (
                f"Every pollutant sits at or below the typical day in this window. "
                f"The closest is {POLLUTANT_NAMES[name]} at {score:+.2f}."
            )
    columns = st.columns(4)
    columns[0].metric("Median AQI", f"{median_aqi:.0f}", help="Middle city's typical day.")
    columns[1].metric(
        "90th percentile AQI",
        f"{peak_aqi:.0f}",
        help="Middle city's bad day. The gap above the median is the volatility.",
    )
    columns[2].metric(
        "Days above 200",
        f"{share:.0f}%",
        help="Middle city's share of days in Poor, Very Poor, or Severe.",
    )
    columns[3].metric("Highest relative pollutant", dominant_label, help=dominant_help)


def render_ahmedabad_notice(stats: pd.DataFrame, cities: list[str]) -> None:
    if "Ahmedabad" not in cities:
        return
    row = stats.loc[stats["City"] == "Ahmedabad"]
    if row.empty:
        st.warning(
            "Ahmedabad is selected, but it has no AQI days in this timeframe. "
            "Its carbon monoxide series was removed because it was a copy of another gas."
        )
        return
    record = row.iloc[0]
    particles = "not measured" if pd.isna(record["pm25_median"]) else f"{record['pm25_median']:.0f}"
    st.warning(
        f"Ahmedabad is in this view. Its median AQI is {record['median']:.0f} and its bad day is "
        f"{record['p90']:.0f}, while its fine-particle median is {particles}. "
        "The carbon monoxide series was removed because it was a copy of another gas. "
        "Do not read Ahmedabad’s AQI as a particle crisis. "
        "The cards use the middle value across the selected cities, so Ahmedabad does not set them while other cities are selected."
    )


def decision_table(headers: list[str], rows: list[list[str]], risk: bool = False) -> str:
    """Compact HTML table. The first cell of a risk row is 'color|label'."""
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            if risk and index == 0:
                color, label = cell.split("|", 1)
                cells.append(f'<td class="group"><span class="dot" style="background:{color}"></span>{label}</td>')
            else:
                cells.append(f"<td>{cell}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    kind = "risk" if risk else "action"
    return (
        f'<table class="decision-table {kind}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_decisions() -> None:
    st.subheader("Who carries the risk")
    st.caption(
        "Groups follow the pollutant that is high, not the map. "
        "Patna is northern and sits with the industrial group. "
        "Amritsar is northern and comparatively clean, so it is not in the winter plan."
    )
    st.markdown(
        decision_table(
            ["Group", "Cities", "Typical January AQI", "What is high", "Decision"],
            [
                [
                    "#E45756|Northern megacities",
                    "Delhi, Lucknow, Gurugram",
                    "356",
                    "Fine particles overnight and in the morning. Nitrogen dioxide at 9 PM",
                    "Winter plan. 9 PM through the next morning",
                ],
                [
                    "#F58518|Industrial hubs",
                    "Ahmedabad, Patna, Talcher, Brajrajnagar, Jorapokhar",
                    "307",
                    "Ahmedabad AQI without matching particles. Coal towns: carbon monoxide, low ozone",
                    "Audit Ahmedabad. Patna joins the winter plan",
                ],
                [
                    "#4C78A8|Southern and coastal",
                    "Bengaluru, Chennai, Mumbai, Hyderabad, Thiruvananthapuram",
                    "126",
                    "A modest 8 AM nitrogen dioxide rise, on a low baseline",
                    "Protect the baseline. Not the crisis group",
                ],
            ],
            risk=True,
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Typical January is the middle city in the group. "
        "The north uses complete years 2017–2019. Industrial and southern cities use 2019, their only full shared year. "
        "Ahmedabad’s own January 2019 AQI was 661, so it does not set the industrial 307."
    )

    st.subheader("What to do next")
    st.markdown(
        decision_table(
            ["Focus", "Action", "When"],
            [
                [
                    "Delhi, Lucknow, Gurugram",
                    "Winter controls from 9 PM through the next morning, on fine particles and the 9 PM nitrogen dioxide peak",
                    "Ready in October. In force November–January",
                ],
                [
                    "Ahmedabad",
                    "Check the AQI monitor before any ranking or public alert",
                    "Within 30 days",
                ],
                [
                    "Patna, Talcher, Brajrajnagar",
                    "Patna follows the winter particle plan. The coal towns need a combustion review. Their carbon monoxide does not fall on weekends",
                    "This quarter",
                ],
                [
                    "All cities",
                    "Do not lead with a weekend ban. The gap is a few AQI points",
                    "Not the primary lever",
                ],
            ],
        ),
        unsafe_allow_html=True,
    )

    st.subheader("Limits of these numbers")
    st.info(
        "2020 ends on 1 July, so it is not a full year. A month is drawn only when it has at least 20 measured days. "
        "Lucknow has no coarse-particle record. Ahmedabad’s carbon monoxide series was removed because it was a copy of another gas. "
        "A blank cell was left blank. Hyderabad has gas spikes locked to the same early-afternoon hour for years."
    )


def show_chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, config={"displayModeBar": "hover", "responsive": True})


def render_clock_table(hour: pd.DataFrame, lines: list[tuple[str, str, list[str]]]) -> None:
    rows = []
    for name, _color, members in lines:
        for pollutant, label in (("PM2.5", "Fine particles"), ("NO2", "Nitrogen dioxide")):
            clock = middle_profile(hour, members, pollutant)
            if clock.empty:
                continue
            rows.append(
                {
                    "Group": name,
                    "Pollutant": label,
                    "Morning crest": clock_level(clock, range(6, 12), True),
                    "Late-evening crest": clock_level(clock, range(16, 24), True),
                    "Night, midnight–5 AM": clock_level(clock, range(0, 6), False),
                    "Midday, noon–3 PM": clock_level(clock, range(12, 16), False),
                }
            )
    if not rows:
        return
    st.markdown("**Where the day peaks**")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "The morning crest is the highest hour from 6 AM to 11 AM. "
        "The late-evening crest is the highest hour from 4 PM to 11 PM. "
        "Night and midday are the middle hour of those stretches. "
        "With more than six cities, each row is the middle city of that group. "
        "Hourly AQI barely moves across the day, so the clock decision uses fine particles and nitrogen dioxide."
    )


STORY_CITIES = [
    "Delhi",
    "Lucknow",
    "Gurugram",
    "Patna",
    "Ahmedabad",
    "Mumbai",
    "Bengaluru",
    "Chennai",
    "Hyderabad",
    "Talcher",
    "Brajrajnagar",
    "Jorapokhar",
    "Thiruvananthapuram",
]


def _year_map(part: pd.DataFrame, months: list[int], column: str = "median") -> dict[int, float]:
    found: dict[int, float] = {}
    for year, year_part in part.groupby("Year"):
        values = year_part.loc[year_part["Month"].isin(months), column]
        if column == "median" and len(months) > 1 and len(values) < 2:
            continue
        if values.empty:
            continue
        found[int(year)] = float(values.median()) if len(months) > 1 else float(values.iloc[0])
    return found


def render_yoy(monthly: pd.DataFrame, day_view: pd.DataFrame, cities: list[str]) -> None:
    """City-by-city January and April–June reading for the cities in view."""
    available = set(monthly["City"])
    show = [city for city in STORY_CITIES if city in available and city in cities][:6]
    if not show:
        return
    particle_months = monthly_medians(day_view.assign(AQI=day_view["PM2.5"]))
    rows = []
    notes = []
    for city in show:
        part = monthly.loc[monthly["City"] == city]
        january = _year_map(part, [1])
        spring = _year_map(part, [4, 5, 6])
        jan_txt = ", ".join(f"{year}: {value:.0f}" for year, value in sorted(january.items())) or "no January with 20 days"
        if 2019 in spring and 2020 in spring:
            spring_txt = f"{spring[2019]:.0f} → {spring[2020]:.0f}"
        else:
            spring_txt = "not enough spring months"
        rows.append({"City": city, "January median AQI": jan_txt, "April–June median AQI": spring_txt})
        sentence = f"**{city}.** January AQI {jan_txt}. April–June {spring_txt}."
        if 2019 in january and 2020 in january and 2019 in spring and 2020 in spring:
            winter_drop = january[2019] - january[2020]
            spring_drop = spring[2019] - spring[2020]
            if spring_drop > 30 and winter_drop < spring_drop:
                sentence += " The spring drop is larger than the January change, so the shutdown is not a new winter baseline."
        if city == "Ahmedabad":
            particles = _year_map(particle_months.loc[particle_months["City"] == city], [1]) if not particle_months.empty else {}
            if 2019 in particles and 2020 in particles and 2019 in january and 2020 in january:
                sentence += (
                    f" January fine particles were {particles[2019]:.0f} then {particles[2020]:.0f}, "
                    "a smaller move than AQI. Treat the AQI move as a monitor question."
                )
        if city == "Mumbai" and 2019 in january and 2020 in january and january[2020] > january[2019]:
            sentence += " January 2020 was higher than January 2019."
        notes.append(sentence)
    st.markdown("**City by city**")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for note in notes:
        st.markdown(note)
    st.caption(
        "January is the winter test, before the late-March 2020 shutdown. "
        "April–June is the shutdown test. A month needs 20 measured days. "
        "2020 has no July through December."
    )


def main() -> None:
    st.set_page_config(
        page_title="Urban Air Quality Intelligence: India 2015–2020",
        page_icon=":material/air:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.4rem; padding-bottom: 2rem;}
        div[data-testid="stMetric"] {
            background: #F6F7F9;
            border: 1px solid #E3E6EA;
            border-radius: 8px;
            padding: 0.7rem 0.9rem;
        }
        table.decision-table { width: 100%; border-collapse: collapse; margin: 0.2rem 0 0.6rem; }
        table.decision-table th {
            text-align: left; font-weight: 600; color: #5c6570; font-size: 0.82rem;
            padding: 0.45rem 0.7rem; border-bottom: 1px solid #E3E6EA;
        }
        table.decision-table td {
            padding: 0.75rem 0.7rem; border-bottom: 1px solid #EEF0F3;
            vertical-align: top; line-height: 1.4;
        }
        table.risk td:nth-child(3) { text-align: right; font-weight: 650; white-space: nowrap; }
        table.action td:nth-child(3) { white-space: nowrap; }
        table.decision-table .dot {
            display: inline-block; width: 0.55rem; height: 0.55rem;
            border-radius: 50%; margin-right: 0.45rem; position: relative; top: 0.05rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    try:
        day = load_day(str(find_data("city_day.csv")))
        hour = load_hour(str(find_data("city_hour.csv")))
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.title("Urban Air Quality Intelligence: India 2015–2020")
    st.caption(
        "The decision is a winter plan for Delhi, Lucknow, and Gurugram, from 9 PM through the next morning. "
        "Ahmedabad’s AQI is not a particle ranking until the monitor is checked."
    )

    with st.sidebar:
        st.header("Focus")
        chosen_groups = st.multiselect(
            "City groups",
            options=list(ARCHETYPES),
            default=[
                "Northern megacities",
                "Industrial and sulfur hubs",
                "Clean coastal and southern hubs",
            ],
            help="Coal and mining belts is the narrower cut of Talcher and Brajrajnagar. Selecting it with the industrial group does not double-count them.",
        )
        pool = sorted({city for group in chosen_groups for city in ARCHETYPES[group]})
        cities = st.multiselect(
            "Cities",
            options=pool,
            default=pool,
            help="The list follows the groups selected above.",
        )
        timeframe = st.radio(
            "Timeframe",
            options=["Common window", "Full history"],
            format_func=lambda value: (
                "Common overlapping window (Jan 2019 – Jul 2020)"
                if value == "Common window"
                else "Full historical record (2015 – Jul 2020)"
            ),
            index=0,
        )
        st.divider()
        st.caption(
            "The common window is the fair comparison: every selected city is judged on the same months. "
            "Full history keeps each city’s own start date, so a late city is not a clean city."
        )
        st.caption(
            "Patna sits with the northern cities because its winter particles match them. "
            "On sulfur dioxide it is closer to Ahmedabad."
        )

    if not cities:
        st.info("Select at least one city in the sidebar.")
        st.stop()

    full_history = timeframe == "Full history"
    window_label = (
        "Full history, 2015 – 1 Jul 2020"
        if full_history
        else "Common window, 1 Jan 2019 – 1 Jul 2020"
    )
    day_view = apply_window(day.loc[day["City"].isin(cities)], "Date", full_history)
    hour_view = apply_window(hour.loc[hour["City"].isin(cities)], "Datetime", full_history)
    # Scores use every city in the window, so a selected city’s Z-score is against the window, not against itself.
    score_base = apply_window(day, "Date", full_history)
    stats = city_aqi_stats(day_view)
    scores = pollutant_scores(score_base)

    render_ahmedabad_notice(stats, cities)
    if stats.empty:
        st.warning("None of the selected cities have an AQI reading in this timeframe.")
        st.stop()

    render_kpis(stats, scores, window_label)

    severity, fingerprint, season, clock = st.tabs(
        ["Severity", "Pollutant fingerprint", "Season and year", "Hour of day"]
    )

    with severity:
        st.subheader("Typical day and bad day")
        show_chart(severity_chart(stats))
        st.caption(
            "Horizontal axis is AQI. Cities are ordered by the bad day, highest at the top. "
            "Hover a median bar for that city’s fine-particle median. "
            f"Source: cleaned city_day · {window_label}."
        )
        if "Ahmedabad" in set(stats["City"]):
            st.caption(
                "Ahmedabad’s red bar is an AQI reading. Its fine-particle median does not sit with Delhi or Patna. "
                "Use the fingerprint tab before treating that bar as the same problem."
            )

    with fingerprint:
        st.subheader("Which pollutant is actually high")
        selected_scores = scores.loc[scores.index.intersection(cities)]
        if selected_scores.notna().sum().sum() == 0:
            st.info("Not enough measured days to score pollutants for this selection.")
        else:
            order = stats["City"].tolist()
            show_chart(heatmap_chart(selected_scores, order))
            st.caption(
                "Each cell is the city’s median Z-score: how far its typical day sits from the typical day "
                "of that pollutant in the selected timeframe. Red is higher. Blue is lower. "
                "A gray cell is a missing series, not a clean reading. "
                "Ahmedabad carbon monoxide is gray because that series was removed. "
                "Lucknow coarse particles are gray because that series was never recorded. "
                f"Source: cleaned city_day · {window_label}."
            )

    with season:
        st.subheader("The year repeats, and 2020 does not erase the winter")
        monthly = monthly_medians(day_view)
        if monthly.empty:
            st.info("No month in this selection has 20 or more AQI days.")
        else:
            show_chart(season_chart(monthly, [city for city in cities if city in set(monthly["City"])]))
            st.caption(
                "Each line is a year. Darker red is a later year. A month needs 20 measured days, so 2020 stops in June. "
                "Red bands are winter. The green band is the monsoon. "
                "With two to six cities, each city has its own panel and its own scale. "
                "With more than six, the line is the middle city. "
                f"Source: cleaned city_day · {window_label}."
            )
            st.markdown(
                """
                **How to read a panel**
                - The shape is the finding: January and December are high, the monsoon is low, every year.
                - A darker line sitting under an older line in January means the winter eased. It did not disappear.
                - The drop after March 2020 is the shutdown. Compare it with January of the same year before calling it a new baseline.
                - Ahmedabad’s AQI moves much further than its fine particles. Read that row with the warning at the top.
                """
            )
            render_yoy(monthly, day_view, cities)

    with clock:
        st.subheader("Late evening is the nitrogen dioxide peak. Mid-afternoon is the clean window.")
        if hour_view.empty:
            st.info("No hourly rows in this selection.")
        else:
            lines = chart_lines(cities, chosen_groups)
            show_chart(diurnal_chart(hour_view, lines))
            if len(cities) > 6:
                st.caption("More than six cities are selected, so each line is the middle city of its group.")
            st.caption(
                "Hour on the horizontal axis. The red band is 8 PM to midnight. The green band is 2 PM to 5 PM. "
                "A clock hour needs 30 readings. Northern nitrogen dioxide crests around 9 PM, inside the red band. "
                "Fine particles are already high overnight and lowest in the mid-afternoon. "
                f"Source: cleaned city_hour · {window_label}."
            )
            render_clock_table(hour_view, lines)

    st.divider()
    with st.container(border=True):
        render_decisions()


if __name__ == "__main__":
    main()
