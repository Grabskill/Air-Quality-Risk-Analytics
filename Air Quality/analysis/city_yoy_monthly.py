"""Year-over-year monthly AQI for individual cities, on each city's own record.

Year and month come from model/csv/dim_date.csv. Cities are not cut to a
shared window. A month is drawn only when that city has at least MIN_DAYS
measured values, so July 2020 (one day) cannot set a point. 2020 itself
ends on 1 July, so its line stops in June.

The six cities are one from each profile we have already separated:
Delhi and Lucknow (particle-heavy north), Ahmedabad and Patna (sulfur and
combustion gas), Bengaluru and Mumbai (southern and coastal, lower level).
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

ROOT = Path(__file__).resolve().parents[1]
DAY_DATA = ROOT / "cleaned files" / "city_day.csv"
DATE_DATA = ROOT / "model" / "csv" / "dim_date.csv"
AQI_OUT = Path(__file__).resolve().parent / "city_yoy_monthly_aqi.png"
PM_OUT = Path(__file__).resolve().parent / "city_yoy_monthly_pm25.png"

MIN_DAYS = 20
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CITIES = ["Delhi", "Lucknow", "Ahmedabad", "Patna", "Bengaluru", "Mumbai"]
METRICS = ("AQI", "PM2.5")


def load() -> pd.DataFrame:
    days = pd.read_csv(DAY_DATA, usecols=["City", "Date", *METRICS])
    days["Date"] = pd.to_datetime(days["Date"])
    calendar = pd.read_csv(DATE_DATA, usecols=["full_date", "year", "month"])
    calendar["full_date"] = pd.to_datetime(calendar["full_date"])
    merged = days.merge(calendar, left_on="Date", right_on="full_date", how="left")
    if merged["year"].isna().any():
        raise SystemExit("Some city-days have no row in dim_date")
    return merged


def monthly(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Median of one metric for every city, year, and month with enough days."""
    counted = (
        frame.groupby(["City", "year", "month"], sort=True)[metric]
        .agg(median="median", days="count")
        .reset_index()
    )
    kept = counted.loc[counted["days"] >= MIN_DAYS].copy()
    kept["year"] = kept["year"].astype(int)
    kept["month"] = kept["month"].astype(int)
    return kept


def plot_grid(stats: pd.DataFrame, metric: str, path: Path) -> None:
    years = list(range(int(stats["year"].min()), int(stats["year"].max()) + 1))
    # Light early years, dark recent years, so 2019 and 2020 are the lines that stand out.
    palette = dict(zip(years, sns.color_palette("rocket_r", n_colors=len(years))))
    sns.set_theme(style="whitegrid", font_scale=0.85)
    fig, axes = plt.subplots(2, 3, figsize=(12.6, 7.6), sharex=True)
    for ax, city in zip(axes.ravel(), CITIES):
        city_stats = stats.loc[stats["City"] == city]
        for year in years:
            part = city_stats.loc[city_stats["year"] == year].set_index("month").reindex(range(1, 13))
            if part["median"].notna().sum() == 0:
                continue
            # A missing month is NaN, so the line breaks instead of bridging a gap.
            ax.plot(
                part.index,
                part["median"],
                color=palette[year],
                linewidth=1.8,
                marker="o",
                markersize=3.2,
                label=str(year),
            )
        ax.set_title(city, loc="left", fontsize=12)
        if ax in (axes[0, 0], axes[1, 0]):
            ax.set_ylabel(f"Median {metric}")
        ax.set_xticks(range(1, 13))
        ax.set_xticklabels(MONTHS, rotation=45, ha="right")
        ax.set_xlim(0.5, 12.5)
        ax.grid(axis="x", visible=False)
    handles = [
        plt.Line2D([0], [0], color=palette[year], marker="o", linewidth=1.8, markersize=3.5, label=str(year))
        for year in years
    ]
    fig.legend(handles=handles, loc="upper center", ncol=len(years), frameon=False, bbox_to_anchor=(0.5, 0.98))
    fig.suptitle(
        f"Monthly median {metric} by year\n"
        f"Each city uses its own full record. A month needs {MIN_DAYS} measured days. 2020 ends in June.",
        fontsize=13,
        y=1.03,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def month_value(stats: pd.DataFrame, city: str, year: int, month: int) -> float:
    hit = stats.loc[(stats["City"] == city) & (stats["year"] == year) & (stats["month"] == month), "median"]
    return float(hit.iloc[0]) if len(hit) else float("nan")


def half_year(stats: pd.DataFrame, city: str, year: int, months: range) -> float:
    """Median of the monthly medians, so one long month does not outweigh another."""
    values = [month_value(stats, city, year, month) for month in months]
    series = pd.Series(values).dropna()
    return float(series.median()) if len(series) else float("nan")


def january_table(stats: pd.DataFrame, metric: str) -> None:
    january = stats.loc[stats["month"] == 1, ["City", "year", "median"]]
    wide = january.pivot(index="City", columns="year", values="median").reindex(CITIES)
    print(f"January median {metric}")
    print(wide.round(0).to_string())
    print()


def print_summary(aqi: pd.DataFrame, pm: pd.DataFrame) -> None:
    print(f"Months with at least {MIN_DAYS} days. 2020 is January–June only.")
    print()
    january_table(aqi, "AQI")
    january_table(pm, "PM2.5")
    rows = []
    for city in CITIES:
        rows.append(
            {
                "City": city,
                "Apr–Jun 2019": half_year(aqi, city, 2019, range(4, 7)),
                "Apr–Jun 2020": half_year(aqi, city, 2020, range(4, 7)),
            }
        )
    print("April–June median AQI. Each number is the median of the three monthly medians.")
    print(pd.DataFrame(rows).round(0).to_string(index=False))
    print()
    print("January is the winter test. It falls before the late-March 2020 lockdown.")
    print("April–June 2020 is the lockdown test. A drop only in those months is a shutdown period, not a new winter baseline.")


def main() -> None:
    frame = load()
    chosen = frame.loc[frame["City"].isin(CITIES)]
    aqi = monthly(chosen, "AQI")
    pm = monthly(chosen, "PM2.5")
    print_summary(aqi, pm)
    plot_grid(aqi, "AQI", AQI_OUT)
    plot_grid(pm, "PM2.5", PM_OUT)
    print()
    print(f"wrote {AQI_OUT}")
    print(f"wrote {PM_OUT}")


if __name__ == "__main__":
    main()
