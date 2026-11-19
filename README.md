# IntelliSched (PBL)

A local educational simulator for CPU scheduling, resource management, historical system monitoring, and a role-protected dashboard.

## Current step

The C++17 console program and Flask dashboard can both create, update, terminate, and monitor processes before scheduling. They compare FCFS, Round Robin, non-preemptive Priority, and non-preemptive SJF scheduling, calculate response, waiting, and turnaround times, save simulated process-state transitions, simulate Banker's resource releases in a safe sequence, and save complete runs to MySQL. The dashboard has user/admin access, queue playback, and CSV reports. A separate Python monitor records local CPU/RAM usage, forecasts 60-second load, and stores monitoring history for dashboard trends and alerts. Python analytics compares saved runs and scans historical scheduling and resource metrics. In the process simulator, WAITING means waiting for CPU time; I/O blocking is not modeled yet.

## Build and run

Follow `database/README.md` to build and run the C++ program with MySQL. Follow `analysis/README.md` to run the analytics report. Follow `dashboard/README.md` to launch the dashboard.

## Project roadmap

1. CPU scheduling and Banker's resource safety analysis (complete)
2. MySQL run persistence (complete)
3. Python analytics (implemented)
4. Web dashboard and simulation form (implemented; can create and save runs)
