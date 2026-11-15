"""Seasonal AQI for three city groups, each on its own shared calendar.

A group is scored only on dates every member city was already recording,
and only on complete Jan–Dec years inside that shared stretch. A partial
year is left out so January–July are not averaged more often than the
monsoon months.

The group number for a month is the median across cities, not a pool of
days. One city with an extreme monitor cannot set the line by having more
rows. The 90th percentile is computed inside each city first, then the
median of those city figures is the group's spike line.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "cleaned files" / "city_day.csv"
OUT = Path(__file__).resolve().parent / "group_seasonal_aqi.png"
# A city-month needs this many measured AQI days before it votes.
MIN_DAYS = 20
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Membership follows the pollution profile, not the map.
# Patna is in the north, and its profile is sulfur and combustion gas, so it
# sits with the industrial hubs. Amritsar is also in the north, and its
# profile is quiet, so it is not placed with Delhi.
GROUPS = {
    "Northern megacities": ["Delhi", "Lucknow", "Gurugram"],
    "Industrial and sulfur hubs": ["Ahmedabad", "Patna", "Talcher", "Brajrajnagar", "Jorapokhar"],
    "Southern and coastal clean hubs": ["Bengaluru", "Chennai", "Mumbai", "Hyderabad", "Thiruvananthapuram"],
}
HELD_OUT = {
    "Amritsar": "Northern city with a clean profile. Left out of the megacity line.",
    "Jaipur": "Northern city whose high pollutant is ozone, not particles.",
    "Kolkata": "Eastern city. The typical day is lower; the hard season is winter only.",
    "Visakhapatnam": "Coastal and mostly low. Left out so the clean hub stays the five named cities.",
    "Amaravati": "Southern and low. Same reason: the clean hub stays the five named cities.",
    "Coimbatore": "Southern and low, but recording starts 13 Jun 2019, which would erase the only full year.",
    "Guwahati": "Northeastern record starts 17 Feb 2019, so it has no complete shared year.",
    "Shillong": "Record starts 28 Aug 2019. Too short for a full seasonal cycle.",
    "Chandigarh": "Record starts 3 Sep 2019. Too short for a full seasonal cycle.",
    "Bhopal": "Record starts 18 Sep 2019. Too short for a full seasonal cycle.",
    "Kochi": "Record starts 23 Jan 2020. Too short for a full seasonal cycle.",
    "Ernakulam": "Record starts 25 Jan 2020. Too short for a full seasonal cycle.",
    "Aizawl": "Record starts 12 Mar 2020. Too short for a full seasonal cycle.",
}
COLORS = {
    "Northern megacities": "#E45756",
    "Industrial and sulfur hubs": "#F58518",
    "Southern and coastal clean hubs": "#4C78A8",
}


def complete_years(start: pd.Timestamp, end: pd.Timestamp) -> list[int]:
    """Calendar years whose 1 Jan and 31 Dec both sit inside the overlap."""
    years = []
    for year in range(start.year, end.year + 1):
        if start <= pd.Timestamp(year, 1, 1) and end >= pd.Timestamp(year, 12, 31):
            years.append(year)
    return years


def city_month_stats(part: pd.DataFrame) -> pd.DataFrame:
    """Median and 90th percentile AQI for one city in one month."""
    return pd.Series(
        {
            "days": int(part.shape[0]),
            "median": part["AQI"].median(),
            "p90": part["AQI"].quantile(0.9),
        }
    )


def main() -> None:
    df = pd.read_csv(DATA, usecols=["City", "Date", "AQI"])
    df["Date"] = pd.to_datetime(df["Date"])
    aqi = df.loc[df["AQI"].notna(), ["City", "Date", "AQI"]].copy()

    print("Where the other cities go")
    roster = pd.DataFrame(
        [{"City": city, "Decision": reason} for city, reason in HELD_OUT.items()]
    )
    print(roster.to_string(index=False))
    print()
    print("Patna is placed in Industrial and sulfur hubs. It is a northern city whose high pollutants are SO2 and other combustion gases.")
    print()

    group_curves = []
    for group, cities in GROUPS.items():
        ranges = []
        for city in cities:
            dates = aqi.loc[aqi["City"] == city, "Date"]
            ranges.append({"City": city, "AQI start": dates.min().date(), "AQI end": dates.max().date(), "AQI days": int(dates.shape[0])})
        ranges = pd.DataFrame(ranges)
        start = pd.Timestamp(ranges["AQI start"].max())
        end = pd.Timestamp(ranges["AQI end"].min())
        years = complete_years(start, end)
        print(group)
        print(ranges.to_string(index=False))
        print(f"Shared window: {start.date()} to {end.date()}")
        if years:
            print(f"Full calendar years used: {', '.join(map(str, years))}")
            print("Partial years at the edges are left out, so each month is counted the same number of times.")
            kept = aqi.loc[aqi["City"].isin(cities) & aqi["Date"].dt.year.isin(years)].copy()
        else:
            print("No full calendar year sits inside the shared window. Months are reported from the window as it stands.")
            kept = aqi.loc[aqi["City"].isin(cities) & (aqi["Date"] >= start) & (aqi["Date"] <= end)].copy()
        print()

        kept["Month"] = kept["Date"].dt.month
        stats = (
            kept.groupby(["City", "Month"], sort=True)
            .apply(city_month_stats, include_groups=False)
            .reset_index()
        )
        # A month with no rows never appears in the table above. Collect those
        # zeros separately so a silent gap is printed with the thin months.
        present = set(zip(stats["City"], stats["Month"]))
        missing_rows = [
            {"City": city, "Month": month, "days": 0, "median": float("nan"), "p90": float("nan")}
            for city in cities
            for month in range(1, 13)
            if (city, month) not in present
        ]
        thin_rows = stats.loc[stats["days"] < MIN_DAYS, ["City", "Month", "days", "median", "p90"]].to_dict("records")
        report = pd.DataFrame(thin_rows + missing_rows)
        stats = stats.loc[stats["days"] >= MIN_DAYS]
        if len(report):
            report = report.sort_values(["City", "Month"])
            print(f"City-months under {MIN_DAYS} AQI days, left out of the average:")
            print(report.to_string(index=False))
            print()

        grouped = stats.groupby("Month").agg(
            cities=("City", "nunique"),
            median=("median", "median"),
            p90=("p90", "median"),
            city_low=("median", "min"),
            city_high=("median", "max"),
        )
        print("Monthly seasonal summary. Median and 90th percentile are the median across cities.")
        show = grouped.copy()
        show.index = [MONTHS[m - 1] for m in show.index]
        print(show.round(1).to_string())
        print()

        curve = grouped.reset_index()
        curve["Group"] = group
        curve["Years"] = ", ".join(map(str, years)) if years else f"{start.date()} to {end.date()}"
        group_curves.append(curve)

    curves = pd.concat(group_curves, ignore_index=True)

    sns.set_theme(style="whitegrid", font_scale=0.95)
    fig, axes = plt.subplots(2, 1, figsize=(12.4, 8.4), sharex=True)
    for group, part in curves.groupby("Group", sort=False):
        years = part["Years"].iloc[0]
        label = f"{group} ({years})"
        color = COLORS[group]
        axes[0].plot(part["Month"], part["median"], color=color, linewidth=2.2, marker="o", label=label)
        axes[0].fill_between(part["Month"], part["city_low"], part["city_high"], color=color, alpha=0.15)
        axes[1].plot(part["Month"], part["p90"], color=color, linewidth=2.2, marker="o", label=label)

    for ax in axes:
        ax.axvspan(0.5, 2.5, color="#F4E4E4", zorder=0)
        ax.axvspan(11.5, 12.5, color="#F4E4E4", zorder=0)
        ax.axvspan(5.5, 9.5, color="#E5F0E8", zorder=0)
        ax.set_xlim(0.5, 12.5)
        ax.set_xticks(range(1, 13))
        ax.grid(axis="x", visible=False)

    axes[0].set_ylabel("Median AQI")
    axes[1].set_ylabel("90th percentile AQI")
    axes[1].set_xlabel("Month")
    axes[1].set_xticklabels(MONTHS)
    # Season names sit in the shaded bands. y is a fraction of the panel height,
    # so the labels stay put when one group's range stretches the scale.
    for x, label, color, ha in (
        (1.2, "Winter", "#8C3A3A", "left"),
        (7.5, "Monsoon", "#2E6B45", "center"),
        (12.35, "Winter", "#8C3A3A", "right"),
    ):
        axes[0].text(
            x, 0.96, label, transform=axes[0].get_xaxis_transform(),
            color=color, fontsize=9, ha=ha, va="top",
        )

    handles, labels = axes[0].get_legend_handles_labels()
    axes[0].legend().remove()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.90), ncol=3, frameon=False)

    # The wide orange band is one city. Point at April, where the gap is easy to see.
    industrial = curves.loc[curves["Group"] == "Industrial and sulfur hubs"]
    april = industrial.loc[industrial["Month"] == 4].iloc[0]
    axes[0].annotate(
        "Upper edge is Ahmedabad",
        xy=(april["Month"], april["city_high"]),
        xytext=(5.1, april["city_high"] + 15),
        color=COLORS["Industrial and sulfur hubs"],
        fontsize=9,
        arrowprops={"arrowstyle": "-", "color": COLORS["Industrial and sulfur hubs"]},
    )

    north = curves.loc[curves["Group"] == "Northern megacities"]
    clean = curves.loc[curves["Group"] == "Southern and coastal clean hubs"]
    peak = north.loc[north["median"].idxmax()]
    trough = clean.loc[clean["median"].idxmin()]
    axes[0].annotate(
        f"Peak {MONTHS[int(peak['Month']) - 1]}  {peak['median']:.0f}",
        xy=(peak["Month"], peak["median"]),
        xytext=(1.7, peak["median"] + 28),
        color=COLORS["Northern megacities"],
        fontsize=9,
        arrowprops={"arrowstyle": "-", "color": COLORS["Northern megacities"]},
    )
    axes[0].annotate(
        f"Low {MONTHS[int(trough['Month']) - 1]}  {trough['median']:.0f}",
        xy=(trough["Month"], trough["median"]),
        xytext=(trough["Month"] + 0.45, 18),
        color=COLORS["Southern and coastal clean hubs"],
        fontsize=9,
        arrowprops={"arrowstyle": "-", "color": COLORS["Southern and coastal clean hubs"]},
    )

    fig.suptitle(
        "Seasonal AQI wave by city group\n"
        "The line is the middle city. Shading runs from the lowest city median to the highest.",
        fontsize=13,
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(OUT, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
