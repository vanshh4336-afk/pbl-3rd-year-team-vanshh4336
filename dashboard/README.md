# IntelliSched dashboard

This local Flask dashboard reads and writes the IntelliSched MySQL database. It requires a signed-in account. Users can view saved runs and create simulations; admins can also create accounts, change roles, and activate or deactivate accounts. Passwords are stored as Werkzeug one-way password hashes. The **New simulation** page lets a user create, edit, monitor, or terminate processes before submitting a run. On submission, it calculates FCFS, Round Robin, non-preemptive Priority, non-preemptive Shortest Job First (SJF), and the Banker's safety result, then saves the complete run. SJF selects the shortest burst among processes that have arrived; ties go to the earlier arrival, then input order. The dashboard reports response, waiting, and turnaround times. Response time is the delay until a process first receives CPU time. It shows each process's `NEW → READY → WAITING → RUNNING → TERMINATED` flow; here `WAITING` means delayed while waiting for CPU time, since the simulator does not yet model I/O blocking. For safe runs, it also shows the Banker's hypothetical resource release at every step: a process's allocated units return to the temporary available pool when it finishes. This safety simulation does not change the saved allocation inputs. The dashboard compares average scheduling metrics plus derived CPU utilization, and shows resource availability and allocations.

For each saved run, **Process queue snapshot** replays the selected algorithm on a simulation-time slider and groups processes as New, Ready, Running, or Terminated. Round Robin replay uses its saved quantum. The state transitions are also stored in `process_state_history` alongside the run; `WAITING` here means delayed for CPU service, since I/O blocking is not modeled. This is a visualization of the simulation schedule, not live operating-system process control. **Download CSV report** exports each process's inputs and scheduling times for every algorithm in that run.

## Start it

Open PowerShell in the project folder, apply the schema, and create the initial administrator account:

```powershell
py -m pip install -r requirements.txt
py -m database.apply_schema
py -m dashboard.create_admin
```

The setup command prompts for a username and password and only creates the first admin when the `users` table is empty. Start the dashboard:

```powershell
py -m dashboard.app
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in. The administrator can then use **Admin users** to create user and admin accounts. For a stable session key across restarts, set `INTELLISCHED_SECRET_KEY` in the PowerShell environment before launching. Use **＋ New simulation** in the top navigation to create another run from the browser. Resource totals are calculated as available units plus units already allocated across processes. Maximum claims must be at least the current allocation and cannot exceed the resource total. Stop the dashboard with `Ctrl+C` in PowerShell.

## Monitor this computer's CPU and RAM

Run the updated `database/schema.sql` once in MySQL Workbench, then install dependencies. Start the monitor in a second PowerShell window and leave it running while you use the dashboard:

```powershell
py -m pip install -r requirements.txt
py -m monitoring.monitor_system --interval 5
```

The monitor records whole-computer CPU percentage and physical RAM usage every five seconds in MySQL. The dashboard summarizes the last 12 readings with average CPU/RAM levels, a rising/stable/falling direction (comparing earlier and later readings), and the count of high-load forecasts. It also applies Scikit-learn's Isolation Forest to up to 100 recent CPU/RAM pairs to flag readings that differ from the historical pattern; at least 10 readings and some variation are required. These are possible anomalies for review, not proof of a fault. The separate 60-second forecast uses linear regression after at least 10 samples. Forecasts are clamped to 0–100%; a forecast is marked high load if CPU or RAM is predicted to reach 80%. At that threshold, the dashboard and monitor terminal show an alert. The dashboard suggests delaying the lowest-priority process in the selected saved run (or gives general advice when no run is selected); it never changes a schedule automatically. Press `Ctrl+C` in the monitor window to stop collection. This monitors the computer where the script runs; it does not inspect or change processes on a remote server.

The server binds to `127.0.0.1`, so it is intended for access from this computer. Python database connection settings are centralized in `analysis/db_config.py`.
