# Python analytics

The analytics report reads saved simulation and Banker data from the `intellisched` MySQL database. It summarizes the latest run and, after enough run history exists, uses Scikit-learn's Isolation Forest to flag unusual per-algorithm metrics.

## Install dependencies

In PowerShell, from the project folder:

```powershell
py -m pip install -r requirements.txt
```

## Run the report

```powershell
py analysis\analyze_runs.py
```

The script connects to MySQL using the shared settings in `analysis/db_config.py`. It reports average response, waiting, and turnaround times, derived CPU utilization, and the latest Banker safety result. The dashboard uses the same connection settings.

Isolation Forest needs at least 10 saved runs for each scheduling algorithm. Until then, the report prints how many runs are available and skips anomaly labels; it does not treat a small sample as enough evidence.
