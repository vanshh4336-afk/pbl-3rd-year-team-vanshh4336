# Local system monitoring

`monitor_system.py` samples whole-computer CPU and physical RAM usage and writes the timestamped readings to the `system_metrics` MySQL table. After 10 or more readings, it fits a linear regression model to the latest 12 samples and writes a 60-second forecast to `system_load_predictions`. The Flask dashboard shows the latest values, up to ten recent samples, and the latest forecast.

## Setup and run

1. Execute the updated `database/schema.sql` in MySQL Workbench so the `system_metrics` table exists.
2. Install the project dependencies with `py -m pip install -r requirements.txt`.
3. From the project directory, run `py -m monitoring.monitor_system --interval 5`.
4. Keep the monitor running in one PowerShell window and the dashboard running in another. Stop monitoring with `Ctrl+C`.

The default interval is five seconds; the minimum is one second. The dashboard summarizes the last 12 stored readings by average CPU/RAM, recent direction (rising, stable, or falling), and the number of high-load forecasts. It also runs Isolation Forest on up to 100 recent CPU/RAM pairs to flag unusual historical readings (minimum 10 readings with some variation); review these as possible anomalies, not confirmed faults. The separate forecast is a short linear extrapolation of recent CPU and RAM percentages, bounded to 0–100%. CPU or RAM forecasts at 80% or above are marked as predicted high load and produce an alert in the monitor terminal and dashboard. The dashboard suggests delaying the lowest-priority process in the selected simulation; this is a rule-based suggestion and does not alter execution or saved schedules. This is a lightweight educational forecast, not a long-range capacity model. The monitor records aggregate CPU and RAM metrics for the local computer only. It does not collect command lines or details about individual operating-system processes.
