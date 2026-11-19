from __future__ import annotations

import sys
from pathlib import Path

import mysql.connector
import pandas as pd
from sklearn.ensemble import IsolationForest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db_config import DB_CONFIG

MIN_HISTORY_PER_ALGORITHM = 10

RUN_METRICS_SQL = """
SELECT
    sr.run_id,
    sr.algorithm,
    AVG(sr.waiting_time) AS average_waiting_time,
    AVG(sr.turnaround_time) AS average_turnaround_time,
    AVG(sr.response_time) AS average_response_time,
    SUM(p.burst_time) / NULLIF(MAX(sr.completion_time), 0) AS cpu_utilization
FROM scheduling_results AS sr
JOIN processes AS p
  ON p.run_id = sr.run_id AND p.process_id = sr.process_id
GROUP BY sr.run_id, sr.algorithm
ORDER BY sr.run_id, sr.algorithm
"""

LATEST_SAFETY_SQL = """
SELECT run_id, is_safe, safe_sequence
FROM banker_checks
ORDER BY run_id DESC, check_id DESC
LIMIT 1
"""


def load_data() -> tuple[pd.DataFrame, dict | None]:
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute(RUN_METRICS_SQL)
            metrics = pd.DataFrame(cursor.fetchall())
            cursor.execute(LATEST_SAFETY_SQL)
            safety = cursor.fetchone()
    finally:
        connection.close()
    return metrics, safety


def print_latest_run(metrics: pd.DataFrame, safety: dict | None) -> None:
    latest_run_id = int(metrics["run_id"].max())
    latest = metrics.loc[metrics["run_id"] == latest_run_id].copy()

    print(f"IntelliSched analytics — latest run {latest_run_id}")
    print("\nScheduling comparison (averages per process):")
    display = latest[[
        "algorithm",
        "average_waiting_time",
        "average_turnaround_time",
        "average_response_time",
        "cpu_utilization",
    ]].copy()
    display["cpu_utilization"] = display["cpu_utilization"] * 100
    display = display.rename(columns={
        "algorithm": "Algorithm",
        "average_waiting_time": "Avg waiting",
        "average_turnaround_time": "Avg turnaround",
        "average_response_time": "Avg response",
        "cpu_utilization": "CPU utilization %",
    })
    print(display.to_string(index=False, formatters={
        "Avg waiting": "{:.2f}".format,
        "Avg turnaround": "{:.2f}".format,
        "Avg response": "{:.2f}".format,
        "CPU utilization %": "{:.1f}".format,
    }))

    if not latest.empty:
        best = latest.loc[latest["average_waiting_time"].idxmin()]
        print(f"\nLowest average waiting time in this run: {best['algorithm']} ")

    if safety is None:
        print("No Banker safety result is stored yet.")
    elif safety["is_safe"]:
        print(f"Banker's check: SAFE — {safety['safe_sequence']}")
    else:
        print("Banker's check: UNSAFE — review the available resources and claims.")


def report_anomalies(metrics: pd.DataFrame) -> None:
    print("\nHistorical anomaly scan (Isolation Forest):")
    found_any = False

    for algorithm, group in metrics.groupby("algorithm"):
        group = group.dropna(subset=[
            "average_waiting_time",
            "average_turnaround_time",
            "average_response_time",
            "cpu_utilization",
        ]).copy()
        if len(group) < MIN_HISTORY_PER_ALGORITHM:
            print(
                f"  {algorithm}: {len(group)} saved run(s); need "
                f"{MIN_HISTORY_PER_ALGORITHM} per algorithm before scanning."
            )
            continue

        features = group[[
            "average_waiting_time",
            "average_turnaround_time",
            "average_response_time",
            "cpu_utilization",
        ]]
        if len(features.drop_duplicates()) < 2:
            print(f"  {algorithm}: history has no variation to analyze yet.")
            continue

        model = IsolationForest(contamination=0.1, random_state=42, n_estimators=100)
        group["anomaly"] = model.fit_predict(features)
        unusual = group.loc[group["anomaly"] == -1]
        if unusual.empty:
            print(f"  {algorithm}: no unusual runs flagged.")
            continue

        found_any = True
        for _, row in unusual.iterrows():
            print(
                f"  {algorithm}, run {int(row['run_id'])}: unusual historical metrics "
                f"(avg waiting {row['average_waiting_time']:.2f}, "
                f"avg response {row['average_response_time']:.2f}, "
                f"avg turnaround {row['average_turnaround_time']:.2f}, "
                f"CPU utilization {row['cpu_utilization'] * 100:.1f}%)."
            )

    if found_any:
        print("  Review flagged runs; an anomaly is a prompt to inspect, not proof of a problem.")


def main() -> int:
    try:
        metrics, safety = load_data()
    except mysql.connector.Error as error:
        print(f"Could not read IntelliSched data from MySQL: {error}", file=sys.stderr)
        return 1

    if metrics.empty:
        print("No saved simulation runs found. Run IntelliSched and save a run first.")
        return 0

    print_latest_run(metrics, safety)
    report_anomalies(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
