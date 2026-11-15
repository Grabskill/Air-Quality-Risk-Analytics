"""Cluster Indian cities by the pollutants that drive their air quality.

Window: 1 Jan 2019 through 1 Jul 2020. Every daily reading of PM2.5, PM10,
NO2, SO2, CO, and O3 is turned into a Z-score against the mean and standard
deviation of that pollutant across the whole window. A city is then the
median of those daily Z-scores. Hierarchical clustering groups cities whose
six-pollutant profiles look alike.

Missing days are left blank. A median is never filled in. A city joins the
cluster when at least five of the six pollutants have MIN_DAYS measured days.
One structural gap (Lucknow has no PM10; Ahmedabad's CO series was removed)
does not drop the city. A city that is short on most pollutants stays out, so
a late start is not read as clean air.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "cleaned files" / "city_day.csv"
OUT = Path(__file__).resolve().parent / "city_pollutant_clusters.png"
START = "2019-01-01"
END = "2020-07-01"
POLLUTANTS = ["PM2.5", "PM10", "NO2", "SO2", "CO", "O3"]
# About two-thirds of the 548-day window. A shorter record misses a season
# and would pull a city toward whichever months it happens to cover.
MIN_DAYS = 365


def _nan_euclidean(a: np.ndarray, b: np.ndarray) -> float:
    """Euclidean distance on the pollutants both cities actually measured."""
    mask = np.isfinite(a) & np.isfinite(b)
    shared = int(mask.sum())
    if shared == 0:
        return 0.0
    gap = a[mask] - b[mask]
    return float(np.sqrt(gap @ gap * (len(a) / shared)))


def silhouette(features: np.ndarray, labels: np.ndarray) -> float:
    """Mean silhouette for a small city-by-pollutant matrix. No sklearn."""
    distances = pdist(features, metric=_nan_euclidean)
    # Square form is easier to index by city.
    n = features.shape[0]
    dist = np.zeros((n, n))
    dist[np.triu_indices(n, k=1)] = distances
    dist += dist.T

    scores = []
    for i in range(n):
        own = labels == labels[i]
        others = ~own
        # A cluster of one city has no neighbour distance, so it is skipped.
        if own.sum() < 2 or not others.any():
            continue
        a = dist[i, own].sum() / (own.sum() - 1)
        b = min(dist[i, labels == lab].mean() for lab in np.unique(labels) if lab != labels[i])
        scores.append((b - a) / max(a, b))
    return float(np.mean(scores)) if scores else 0.0


def _mean(profile: pd.Series, columns: list[str]) -> float:
    """Mean of the available pollutants. A missing series is skipped."""
    values = profile[columns].to_numpy(dtype=float)
    return float(np.nanmean(values))


# Names follow the branches you can see on the heatmap, not a flat cut into
# three. A three-group cut isolates the two extreme pairs and leaves Delhi in
# the remainder, which hides the particle branch. Thresholds sit in the gap
# the tree actually leaves: SO2 jumps from 0.73 (Talcher, whose CO and ozone
# tell a different story) to 1.33 (Patna); CO with ozone near -1.2 is only
# Talcher and Brajrajnagar; the particle score jumps from about 0.14 to 0.46.
GROUP_ORDER = [
    "Industrial / sulfur",
    "Industrial / carbon monoxide",
    "Particulate-heavy",
    "Lower than the window",
]
GROUP_RATIONALE = {
    "Industrial / sulfur": (
        "SO2 is the red column, and the other combustion gases are high with it. "
        "Particles sit near the window average, so this is not a dust profile. "
        "Ahmedabad is the more extreme of the two."
    ),
    "Industrial / carbon monoxide": (
        "CO is high, ozone is far below the window, and NO2 is low. "
        "High CO without high NO2 is a combustion-town pattern, not a traffic pattern."
    ),
    "Particulate-heavy": (
        "PM2.5 and PM10 are the high pair on the typical day. "
        "Delhi also carries elevated NO2, so traffic gas sits on top of the particle burden there."
    ),
    "Lower than the window": (
        "The typical day is at or below the window average. "
        "This is the median across the whole window, including the monsoon, "
        "so a city can still have a severe winter and land here."
    ),
}


def behavior_group(row: pd.Series) -> str:
    """Assign one city from the pollutant that is actually elevated."""
    particles = _mean(row, ["PM2.5", "PM10"])
    sulfur = float(row["SO2"])
    co = float(row["CO"]) if np.isfinite(row["CO"]) else np.nan
    ozone = float(row["O3"]) if np.isfinite(row["O3"]) else np.nan
    if sulfur >= 1.0:
        return "Industrial / sulfur"
    if np.isfinite(co) and co >= 0.5 and np.isfinite(ozone) and ozone <= -0.8:
        return "Industrial / carbon monoxide"
    if particles >= 0.30:
        return "Particulate-heavy"
    return "Lower than the window"


def main() -> None:
    df = pd.read_csv(DATA, usecols=["City", "Date", *POLLUTANTS])
    df["Date"] = pd.to_datetime(df["Date"])
    window = df.loc[(df["Date"] >= START) & (df["Date"] <= END)].copy()

    # Coverage before any score is computed. A blank cell is a day with no
    # measurement. Nothing is interpolated.
    coverage = window.groupby("City")[POLLUTANTS].apply(lambda g: g.notna().sum())
    print("Measured days by city")
    print(coverage.to_string())
    print()

    # One mean and one std per pollutant, taken over every measured day in the
    # window. ddof=0 treats this window as the population being described.
    means = window[POLLUTANTS].mean()
    stds = window[POLLUTANTS].std(ddof=0)
    print("Window mean and standard deviation")
    scale = pd.DataFrame({"mean": means, "std": stds})
    print(scale.round(2).to_string())
    print()

    z = (window[POLLUTANTS] - means) / stds
    z["City"] = window["City"]

    # Median of the daily Z-scores. Because Z is a rescaling, this equals
    # (city median - window mean) / window std. The median keeps a single
    # extreme day from setting the city's profile.
    rows = []
    for city, part in z.groupby("City"):
        days = coverage.loc[city]
        record = {"City": city}
        for pollutant in POLLUTANTS:
            if int(days[pollutant]) >= MIN_DAYS:
                record[pollutant] = part[pollutant].median()
            else:
                record[pollutant] = np.nan
        rows.append(record)
    profiles = pd.DataFrame(rows).set_index("City")

    measured = profiles.notna().sum(axis=1)
    # Five solid pollutants is enough. Requiring all six would drop Lucknow
    # (no PM10 series) and Ahmedabad (CO removed) and hide the two profiles
    # a CEO most needs to see.
    clustered = profiles.loc[measured >= 5].sort_index()
    held_out = profiles.index.difference(clustered.index)
    print(f"Cities with {MIN_DAYS}+ days on at least five pollutants: {len(clustered)}")
    print(f"Held out: {', '.join(held_out)}")
    print()
    print("City median Z-scores")
    print(clustered.round(2).to_string())
    print()

    # Average linkage on a Euclidean distance that skips a missing pollutant
    # and rescales so a city with one gap is not pulled toward everyone else.
    # Ward linkage needs a complete Euclidean matrix, which these gaps break.
    features = clustered.to_numpy(dtype=float)
    link = linkage(pdist(features, metric=_nan_euclidean), method="average")
    # A flat 3-group cut has the higher silhouette only because Ahmedabad,
    # Patna, Talcher, and Brajrajnagar are far from everyone else. That cut
    # puts Delhi, whose median PM10 Z-score is about +0.9, in the same group
    # as Thiruvananthapuram. The groups below follow the next branch instead.
    scores = {k: silhouette(features, fcluster(link, t=k, criterion="maxclust")) for k in (3, 4)}
    print("Silhouette of a flat cut (higher looks tidier, and here it hides the particle cities)")
    for k, score in scores.items():
        print(f"  {k} groups: {score:.3f}")
    print()

    labels = clustered.apply(behavior_group, axis=1)
    centroids = clustered.groupby(labels).mean()
    summary_rows = []
    for group in GROUP_ORDER:
        members = labels.index[labels == group]
        profile = centroids.loc[group]
        summary_rows.append(
            {
                "Group": group,
                "Cities": ", ".join(sorted(members)),
                "Cities (n)": int(len(members)),
                "PM2.5": profile["PM2.5"],
                "PM10": profile["PM10"],
                "NO2": profile["NO2"],
                "SO2": profile["SO2"],
                "CO": profile["CO"],
                "O3": profile["O3"],
            }
        )
    summary = pd.DataFrame(summary_rows)
    printable = summary.copy()
    for pollutant in POLLUTANTS:
        printable[pollutant] = printable[pollutant].map(lambda v: f"{v:+.2f}")
    print("Pollution behavior groups")
    print("Cell values are the average of city median Z-scores. Positive is higher than the window.")
    print(printable.to_string(index=False))
    print()
    for group in GROUP_ORDER:
        print(f"{group}: {GROUP_RATIONALE[group]}")
    print()
    print("No separate traffic group. NO2 is high inside Ahmedabad's sulfur profile and inside Delhi's particle profile, not as its own cluster.")
    print("Jaipur sits on the particle branch of the tree because ozone is high (+0.73), but its PM2.5 and PM10 are not, so it stays in the lower group.")
    print("Jorapokhar is the sulfur-tilted edge of the lower group (SO2 +0.41, NO2 -0.67). It does not join Ahmedabad and Patna.")
    print()

    if len(held_out):
        print("Held out: fewer than five pollutants have "
              f"{MIN_DAYS} days, so the median would describe a short slice of the window.")
        print(coverage.loc[held_out].to_string())
        print()

    # Column tree uses the same missing-aware distance, so a pollutant a city
    # never recorded does not decide which pollutants sit together.
    col_link = linkage(pdist(features.T, metric=_nan_euclidean), method="average")
    vmax = float(np.nanmax(np.abs(features)))
    sns.set_theme(style="white", font_scale=0.9)
    grid = sns.clustermap(
        clustered,
        cmap="RdBu_r",
        center=0,
        vmin=-vmax,
        vmax=vmax,
        row_linkage=link,
        col_linkage=col_link,
        mask=clustered.isna(),
        figsize=(11, 11),
        linewidths=0.5,
        linecolor="white",
        annot=True,
        fmt=".2f",
        dendrogram_ratio=(0.14, 0.12),
        cbar_kws={"label": "Median Z-score"},
        tree_kws={"linewidths": 1.0},
    )
    # A missing series is gray. White is reserved for a Z-score of zero.
    grid.ax_heatmap.set_facecolor("#D0D0D0")
    grid.ax_heatmap.set_xlabel("Pollutant")
    grid.ax_heatmap.set_ylabel("")
    grid.fig.suptitle(
        "What drives each city: median pollutant Z-score\n1 Jan 2019 – 1 Jul 2020",
        fontsize=14,
        y=1.02,
    )
    grid.savefig(OUT, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(grid.fig)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
