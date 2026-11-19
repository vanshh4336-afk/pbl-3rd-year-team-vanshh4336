from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

import mysql.connector
import psutil
from sklearn.linear_model import LinearRegression

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db_config import DB_CONFIG

MIN_PREDICTION_SAMPLES = 10
HISTORY_WINDOW = 12
FORECAST_HORIZON_SECONDS = 60
HIGH_LOAD_THRESHOLD_PERCENT = 80.0


def save_sample(host_name: str) -> dict:
    """Read current host CPU/RAM usage and store one timestamped sample."""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO system_metrics "
                "(host_name, cpu_percent, memory_percent, memory_used_bytes, memory_total_bytes) "
                "VALUES (%s, %s, %s, %s, %s)",
                (host_name, cpu_percent, memory.percent, memory.used, memory.total),
            )
            metric_id = cursor.lastrowid
        connection.commit()
    finally:
        connection.close()
    return {
        "cpu_percent": cpu_percent,
        "memory_percent": memory.percent,
        "memory_used_bytes": memory.used,
        "memory_total_bytes": memory.total,
        "metric_id": metric_id,
    }


def predict_load(host_name: str, metric_id: int) -> dict:
    """Fit a short linear trend to recent host samples and save a 60s forecast."""
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT captured_at, cpu_percent, memory_percent FROM system_metrics "
                "WHERE host_name=%s ORDER BY metric_id DESC LIMIT %s",
                (host_name, HISTORY_WINDOW),
            )
            samples = list(reversed(cursor.fetchall()))
            if len(samples) < MIN_PREDICTION_SAMPLES:
                return {"sample_count": len(samples), "prediction": None}

            origin = samples[0]["captured_at"]
            elapsed = [[(sample["captured_at"] - origin).total_seconds()]
                       for sample in samples]
            targets = [[float(sample["cpu_percent"]), float(sample["memory_percent"])]
                       for sample in samples]
            model = LinearRegression()
            model.fit(elapsed, targets)
            forecast_at = elapsed[-1][0] + FORECAST_HORIZON_SECONDS
            cpu_forecast, memory_forecast = model.predict([[forecast_at]])[0]
            cpu_forecast = min(100.0, max(0.0, float(cpu_forecast)))
            memory_forecast = min(100.0, max(0.0, float(memory_forecast)))
            high_load = (cpu_forecast >= HIGH_LOAD_THRESHOLD_PERCENT or
                         memory_forecast >= HIGH_LOAD_THRESHOLD_PERCENT)
            cursor.execute(
                "INSERT INTO system_load_predictions "
                "(metric_id, horizon_seconds, sample_count, cpu_predicted_percent, "
                "memory_predicted_percent, high_load_predicted) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (metric_id, FORECAST_HORIZON_SECONDS, len(samples), cpu_forecast,
                 memory_forecast, int(high_load)),
            )
        connection.commit()
        return {
            "sample_count": len(samples),
            "prediction": {
                "cpu_percent": cpu_forecast,
                "memory_percent": memory_forecast,
                "high_load": high_load,
            },
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record this computer's CPU and RAM usage in IntelliSched MySQL."
    )
    parser.add_argument(
        "--interval", type=float, default=5.0,
        help="seconds between samples (minimum 1; default: 5)",
    )
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be at least 1 second")

    host_name = socket.gethostname()
    print(f"Monitoring CPU and RAM on {host_name} every {args.interval:g} seconds.")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            started = time.monotonic()
            sample = save_sample(host_name)
            used_gib = sample["memory_used_bytes"] / (1024 ** 3)
            total_gib = sample["memory_total_bytes"] / (1024 ** 3)
            print(
                f"CPU {sample['cpu_percent']:.1f}% | "
                f"RAM {sample['memory_percent']:.1f}% "
                f"({used_gib:.2f}/{total_gib:.2f} GiB)"
            )
            forecast = predict_load(host_name, sample["metric_id"])
            if forecast["prediction"]:
                prediction = forecast["prediction"]
                print(
                    f"  60s forecast: CPU {prediction['cpu_percent']:.1f}% | "
                    f"RAM {prediction['memory_percent']:.1f}% | "
                    f"high-load predicted: {'yes' if prediction['high_load'] else 'no'}"
                )
                if prediction["high_load"]:
                    print("  ALERT: CPU or RAM is forecast to reach 80% within 60 seconds.")
                    print("  Recommendation: consider delaying low-priority work until usage drops; no schedule was changed.")
            else:
                print(
                    f"  Forecast needs {MIN_PREDICTION_SAMPLES} samples "
                    f"({forecast['sample_count']}/{MIN_PREDICTION_SAMPLES} collected)."
                )
            remaining = args.interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
    except mysql.connector.Error as exc:
        print(f"Could not save a system metric to MySQL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
