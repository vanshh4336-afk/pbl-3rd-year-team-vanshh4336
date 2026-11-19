# MySQL setup and persistence

## 1. Create the database tables

From the project folder, run the schema updater, which applies `schema.sql` and upgrades older scheduling tables (including SJF and response time):

```powershell
py -m database.apply_schema
```

You can also open `schema.sql` in MySQL Workbench and execute it, then apply the legacy scheduling-result migration below if this database predates SJF/response-time support.

If the database already exists, `py -m database.apply_schema` creates missing `users` and `process_state_history` tables for dashboard access control and simulated status history, plus `system_metrics` and `system_load_predictions` for CPU/RAM monitoring and forecasting. Existing tables are retained; a repeatable `INSERT IGNORE` backfills a basic state timeline for older saved runs.

## 2. Build the C++ program with MySQL

Use a 64-bit MinGW-w64 compiler with this 64-bit MySQL client library. From PowerShell in the project folder, run the following. The target check should report `x86_64-w64-mingw32`; use the MSYS2 compiler by its full path because an older `g++` may still be first on PATH.

```powershell
$env:Path = 'C:\msys64\ucrt64\bin;C:\msys64\usr\bin;C:\Program Files\MySQL\MySQL Server 8.0\lib;' + $env:Path
& 'C:\msys64\ucrt64\bin\g++.exe' -dumpmachine
& 'C:\msys64\ucrt64\bin\g++.exe' -std=c++17 -Wall -Wextra -pedantic `
  -I'C:\Program Files\MySQL\MySQL Server 8.0\include' `
  src\main.cpp src\database.cpp `
  'C:\Program Files\MySQL\MySQL Server 8.0\lib\libmysql.lib' `
  -o intellisched.exe
.\intellisched.exe
```

## 3. Save a run

After entering scheduling and resource data, choose `1` when asked to save. The program connects with the MySQL login configured in `src/database.cpp`.

The app saves the run, process inputs, all four scheduling result sets, each process's simulated state transitions, resource totals and allocations, the Banker’s safety result, and a log entry in a single transaction. The dashboard and C++ save path both store the state history. `process_state_history` is event history for a simulated schedule; it is not a live operating-system process monitor. Whole-computer CPU/RAM samples are stored separately in `system_metrics`. The MySQL account needs permission to insert rows in the `intellisched` tables.

The scheduling result algorithm enum must include `SJF`. For an existing database created from an older schema, run this once in MySQL Workbench:

```sql
ALTER TABLE scheduling_results
  MODIFY algorithm ENUM('FCFS', 'ROUND_ROBIN', 'PRIORITY', 'SJF') NOT NULL;
```

For an existing database, add and backfill response time once as well:

```sql
ALTER TABLE scheduling_results
  ADD COLUMN response_time INT UNSIGNED NULL AFTER completion_time;

UPDATE scheduling_results AS sr
JOIN processes AS p
  ON p.run_id = sr.run_id AND p.process_id = sr.process_id
SET sr.response_time = sr.start_time - p.arrival_time;

ALTER TABLE scheduling_results
  MODIFY response_time INT UNSIGNED NOT NULL,
  ADD CONSTRAINT chk_result_response CHECK (response_time <= waiting_time);
```

Response time is the delay from process arrival until it first gets the CPU. The stored `start_time` is the first dispatch time, including for Round Robin.

If MySQL rejects the connection, check that the MySQL80 service is running, the `intellisched` database exists, and the configured account has access to it.

## View saved runs in Workbench

```sql
USE intellisched;
SELECT * FROM simulation_runs ORDER BY run_id DESC;
SELECT * FROM scheduling_results ORDER BY result_id DESC;
SELECT * FROM process_state_history ORDER BY state_history_id DESC;
SELECT * FROM system_metrics ORDER BY metric_id DESC;
SELECT * FROM banker_checks ORDER BY check_id DESC;
```
