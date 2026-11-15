"""Hour-of-day and weekday patterns for the three city groups.

Weekday comes from model/csv/dim_date.csv. Monday is 1 and Sunday is 7.
Saturday and Sunday (6 and 7) are the weekend.

Each group keeps the same complete calendar years used for the seasonal
wave. Northern megacities use 2017–2019. The industrial hubs and the
southern hubs use 2019 only, because that is the only full year every
city in the group was recording.

A group figure is the median across cities. Each city is summarised first,
so a long record or an extreme monitor does not set the result. Ahmedabad
has no CO series in the cleaned file, so the industrial CO number is the
middle of the other industrial cities.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

ROOT = Path(__file__).resolve().parents[1]
HOUR_DATA = ROOT / "cleaned files" / "city_hour.csv"
DATE_DATA = ROOT / "model" / "csv" / "dim_date.csv"
DIURNAL_OUT = Path(__file__).resolve().parent / "group_hourly_diurnal.png"
WEEK_OUT = Path(__file__).resolve().parent / "group_weekday_weekend.png"

POLLUTANTS = ["AQI", "PM2.5", "NO2", "CO"]
# A city must have this many measured hours before it votes in a segment,
# and this many measured readings of one clock hour before that hour votes.
MIN_SEGMENT_HOURS = 200
MIN_CLOCK_HOURS = 30

GROUPS = {
    "Northern megacities": ["Delhi", "Lucknow", "Gurugram"],
    "Industrial and sulfur hubs": ["Ahmedabad", "Patna", "Talcher", "Brajrajnagar", "Jorapokhar"],
    "Southern and coastal clean hubs": ["Bengaluru", "Chennai", "Mumbai", "Hyderabad", "Thiruvananthapuram"],
}
GROUP_YEARS = {
    "Northern megacities": [2017, 2018, 2019],
    "Industrial and sulfur hubs": [2019],
    "Southern and coastal clean hubs": [2019],
}
COLORS = {
    "Northern megacities": "#E45756",
    "Industrial and sulfur hubs": "#F58518",
    "Southern and coastal clean hubs": "#4C78A8",
}
SHORT = {
    "Northern megacities": "Northern\nmegacities",
    "Industrial and sulfur hubs": "Industrial\nhubs",
    "Southern and coastal clean hubs": "Southern\nclean hubs",
}
# Windows used to name a peak. The clock hour itself is chosen from the data.
MORNING = range(6, 12)
EVENING = range(16, 24)
NIGHT = range(0, 6)
MIDDAY = range(12, 16)


def clock(hour: int) -> str:
    return f"{int(hour):02d}:00"


def load_hours() -> pd.DataFrame:
    """Hours for the three groups, with weekday attached from the date table."""
    hours = pd.read_csv(
        HOUR_DATA,
        usecols=["City", "Datetime", *POLLUTANTS],
        parse_dates=["Datetime"],
    )
    dates = pd.read_csv(DATE_DATA, usecols=["full_date", "weekday"])
    dates["full_date"] = pd.to_datetime(dates["full_date"])
    hours["full_date"] = hours["Datetime"].dt.normalize()
    hours = hours.merge(dates, on="full_date", how="left")
    if hours["weekday"].isna().any():
        missing = int(hours["weekday"].isna().sum())
        raise SystemExit(f"{missing} hours have no row in dim_date")

    pieces = []
    for group, cities in GROUPS.items():
        years = GROUP_YEARS[group]
        part = hours.loc[
            hours["City"].isin(cities) & hours["Datetime"].dt.year.isin(years)
        ].copy()
        part["Group"] = group
        part["Segment"] = part["weekday"].map(lambda value: "Weekend" if value >= 6 else "Weekday")
        part["Hour"] = part["Datetime"].dt.hour
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True)


def weekday_table(work: pd.DataFrame) -> pd.DataFrame:
    """Median and 90th percentile by group, with each city voting once."""
    rows = []
    for group, cities in GROUPS.items():
        part = work.loc[work["Group"] == group]
        for segment in ("Weekday", "Weekend"):
            block = part.loc[part["Segment"] == segment]
            for pollutant in POLLUTANTS:
                city_rows = []
                for city, city_block in block.groupby("City"):
                    values = city_block[pollutant].dropna()
                    if len(values) < MIN_SEGMENT_HOURS:
                        print(f"  {group} / {city} / {segment} / {pollutant}: {len(values)} hours, left out")
                        continue
                    city_rows.append({"median": values.median(), "p90": values.quantile(0.9)})
                city_stats = pd.DataFrame(city_rows)
                rows.append(
                    {
                        "Group": group,
                        "Segment": segment,
                        "Pollutant": pollutant,
                        "Cities": int(len(city_stats)),
                        "Median": city_stats["median"].median(),
                        "P90": city_stats["p90"].median(),
                    }
                )
    return pd.DataFrame(rows)


def hourly_profile(work: pd.DataFrame) -> pd.DataFrame:
    """City-equal mean and median for each clock hour."""
    frames = []
    for pollutant in ("AQI", "PM2.5", "NO2"):
        counted = (
            work.groupby(["Group", "City", "Hour"], sort=True)[pollutant]
            .agg(median="median", mean="mean", n="count")
            .reset_index()
        )
        counted = counted.loc[counted["n"] >= MIN_CLOCK_HOURS]
        # Median across cities of the city median, and of the city mean.
        grouped = counted.groupby(["Group", "Hour"], sort=True).agg(
            median=("median", "median"),
            average=("mean", "median"),
            cities=("City", "nunique"),
        )
        grouped["Pollutant"] = pollutant
        frames.append(grouped.reset_index())
    return pd.concat(frames, ignore_index=True)


def peak_report(profile: pd.DataFrame) -> None:
    """Name the morning and evening clock hours against night and midday."""
    print("Clock-hour peaks. Levels are the median across cities of each city's hourly median.")
    for group in GROUPS:
        print(group)
        for pollutant in ("PM2.5", "NO2", "AQI"):
            part = profile.loc[(profile["Group"] == group) & (profile["Pollutant"] == pollutant)]
            def level(hours: range) -> float:
                return float(part.loc[part["Hour"].isin(hours), "median"].median())

            def crest(hours: range) -> pd.Series:
                window = part.loc[part["Hour"].isin(hours)]
                return window.loc[window["median"].idxmax()]

            morning = crest(MORNING)
            evening = crest(EVENING)
            print(
                f"  {pollutant}: morning peak {clock(morning['Hour'])} = {morning['median']:.1f}, "
                f"evening peak {clock(evening['Hour'])} = {evening['median']:.1f}, "
                f"night baseline {level(NIGHT):.1f}, midday baseline {level(MIDDAY):.1f}"
            )
        print()


def plot_diurnal(profile: pd.DataFrame) -> None:
    sns.set_theme(style="whitegrid", font_scale=0.95)
    fig, axes = plt.subplots(2, 1, figsize=(12.2, 8.0), sharex=True)
    panels = (("PM2.5", axes[0]), ("NO2", axes[1]))
    for pollutant, ax in panels:
        part = profile.loc[profile["Pollutant"] == pollutant]
        for group in GROUPS:
            line = part.loc[part["Group"] == group].sort_values("Hour")
            ax.plot(
                line["Hour"],
                line["median"],
                color=COLORS[group],
                linewidth=2.2,
                marker="o",
                markersize=3.5,
                label=group,
            )
        # Commuter windows the peaks are judged against. The printed table
        # names the actual crest, which can sit later than the evening window.
        ax.axvspan(7.5, 10.5, color="#F4E4E4", zorder=0)
        # The NO2 crest is 21:00, after the 17:00–20:00 commute window.
        ax.axvspan(17.5, 22.5, color="#F4E4E4", zorder=0)
        ax.set_ylabel(f"{pollutant}, hourly median")
        ax.set_xlim(-0.5, 23.5)
        ax.grid(axis="x", visible=False)
    axes[0].text(9, 0.96, "Morning", transform=axes[0].get_xaxis_transform(), ha="center", va="top", color="#8C3A3A", fontsize=9)
    axes[0].text(20, 0.96, "Late evening", transform=axes[0].get_xaxis_transform(), ha="center", va="top", color="#8C3A3A", fontsize=9)
    axes[1].set_xlabel("Hour of day")
    axes[1].set_xticks(range(0, 24, 2))
    axes[1].set_xticklabels([clock(hour) for hour in range(0, 24, 2)])
    axes[0].legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    fig.suptitle(
        "Hourly wave by city group\nLine is the middle city's median. Shading marks 08:00–10:00 and 18:00–22:00.",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(DIURNAL_OUT, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_weekday(summary: pd.DataFrame) -> None:
    sns.set_theme(style="whitegrid", font_scale=0.95)
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 5.4))
    palette = {"Weekday": "#4C78A8", "Weekend": "#E45756"}
    for ax, pollutant in zip(axes, ("AQI", "PM2.5")):
        part = summary.loc[summary["Pollutant"] == pollutant].copy()
        part["Group"] = part["Group"].map(SHORT)
        sns.barplot(
            data=part,
            x="Group",
            y="Median",
            hue="Segment",
            hue_order=["Weekday", "Weekend"],
            palette=palette,
            ax=ax,
        )
        for container in ax.containers:
            ax.bar_label(container, fmt="%.0f", padding=2, fontsize=8)
        ax.set_xlabel("")
        ax.set_ylabel(f"Median {pollutant}")
        ax.set_ylim(0, part["Median"].max() * 1.18)
        if ax is not axes[0]:
            ax.legend_.remove()
        else:
            ax.legend(frameon=False, title="")
    fig.suptitle(
        "Weekday and weekend median, by city group\nEach bar is the middle city. Monday–Friday against Saturday–Sunday.",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(WEEK_OUT, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    work = load_hours()
    print("Hours in the analysis")
    print(work.groupby("Group").size().to_string())
    print()
    print("Weekday is Monday–Friday (1–5). Weekend is Saturday–Sunday (6–7), from dim_date.")
    print(f"A city needs {MIN_SEGMENT_HOURS} measured hours to vote.")
    print()
    summary = weekday_table(work)
    show = summary.copy()
    show["Median"] = show["Median"].map(lambda value: f"{value:.1f}")
    show["P90"] = show["P90"].map(lambda value: f"{value:.1f}")
    print(show.to_string(index=False))
    print()

    # Paired gap: within each city, weekend median minus weekday median, then
    # the middle city. A positive number means the weekend is higher.
    print("Paired weekend gap (weekend median minus weekday median), middle city")
    for group in GROUPS:
        part = work.loc[work["Group"] == group]
        for pollutant in POLLUTANTS:
            gaps = []
            for _, city_block in part.groupby("City"):
                weekday = city_block.loc[city_block["Segment"] == "Weekday", pollutant].dropna()
                weekend = city_block.loc[city_block["Segment"] == "Weekend", pollutant].dropna()
                if len(weekday) < MIN_SEGMENT_HOURS or len(weekend) < MIN_SEGMENT_HOURS:
                    continue
                gaps.append(weekend.median() - weekday.median())
            middle = pd.Series(gaps).median()
            print(f"  {group} / {pollutant}: {middle:+.1f}")
    print()

    profile = hourly_profile(work)
    peak_report(profile)
    plot_diurnal(profile)
    plot_weekday(summary)
    print(f"wrote {DIURNAL_OUT}")
    print(f"wrote {WEEK_OUT}")


if __name__ == "__main__":
    main()
