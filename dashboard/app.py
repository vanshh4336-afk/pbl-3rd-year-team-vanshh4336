from __future__ import annotations

import json
import csv
import io
import os
import secrets
import sys
from functools import wraps
from collections import deque
from pathlib import Path

from flask import Flask, abort, flash, g, jsonify, make_response, redirect, render_template, request, session, url_for
import mysql.connector
from sklearn.ensemble import IsolationForest
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db_config import DB_CONFIG

app = Flask(__name__)
app.secret_key = os.environ.get("INTELLISCHED_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def inject_template_auth():
    return {"current_user": getattr(g, "current_user", None), "csrf_token": csrf_token}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        connection = mysql.connector.connect(**DB_CONFIG)
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT user_id, username, role FROM users WHERE user_id=%s AND is_active=TRUE", (user_id,))
                g.current_user = cursor.fetchone()
        finally:
            connection.close()
        if not g.current_user:
            session.clear()
            flash("Your account is inactive. Please contact an administrator.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.current_user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def validate_csrf() -> None:
    supplied = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(supplied, expected):
        abort(400, "Invalid form token. Reload the page and try again.")


def low_priority_recommendation(processes: list[dict], lower_first: bool) -> str:
    """Suggest one low-priority process to defer; this never changes a run."""
    if not processes:
        return "Consider delaying low-priority work until CPU and RAM fall below 80%. No process was changed."
    # For lower-number-first conventions, larger numbers have lower priority.
    chosen = max(processes, key=lambda p: (p["priority"], -p["process_number"])) if lower_first else \
             min(processes, key=lambda p: (p["priority"], p["process_number"]))
    return (f"Consider delaying P{chosen['process_number']} (priority {chosen['priority']}) "
            "until load falls below 80%. This is a suggestion only; the schedule was not changed.")


def summarize_resource_trends(samples: list[dict], high_load_count: int) -> dict:
    """Describe recent host usage trends without treating them as a forecast."""
    if not samples:
        return {"sample_count": 0, "cpu_average": None, "memory_average": None,
                "cpu_direction": "Waiting for readings", "memory_direction": "Waiting for readings",
                "high_load_count": high_load_count}

    def direction(key: str) -> str:
        midpoint = max(1, len(samples) // 2)
        earlier = sum(float(row[key]) for row in samples[:midpoint]) / midpoint
        later_rows = samples[midpoint:] or samples[-1:]
        later = sum(float(row[key]) for row in later_rows) / len(later_rows)
        change = later - earlier
        return "rising" if change >= 1 else "falling" if change <= -1 else "stable"

    return {
        "sample_count": len(samples),
        "cpu_average": sum(float(row["cpu_percent"]) for row in samples) / len(samples),
        "memory_average": sum(float(row["memory_percent"]) for row in samples) / len(samples),
        "cpu_direction": direction("cpu_percent"),
        "memory_direction": direction("memory_percent"),
        "high_load_count": high_load_count,
    }


def detect_resource_anomalies(samples: list[dict]) -> dict:
    """Use an unsupervised model to flag unusual historical CPU/RAM readings."""
    if len(samples) < 10:
        return {"sample_count": len(samples), "anomaly_count": 0,
                "status": f"Need 10 readings; {len(samples)} available"}
    values = [[float(row["cpu_percent"]), float(row["memory_percent"])] for row in samples]
    if len({tuple(row) for row in values}) < 2:
        return {"sample_count": len(samples), "anomaly_count": 0,
                "status": "Not enough variation to identify unusual readings"}
    model = IsolationForest(contamination=0.1, random_state=42, n_estimators=100)
    labels = model.fit_predict(values)
    anomaly_count = int(sum(label == -1 for label in labels))
    return {
        "sample_count": len(samples),
        "anomaly_count": anomaly_count,
        "status": (f"{anomaly_count} unusual reading(s) flagged" if anomaly_count
                   else "No unusual readings flagged"),
    }


def build_queue_playback(processes: list[dict], quantum: int, saved_rows: list[dict]) -> dict:
    """Build CPU intervals for an interactive, simulated queue timeline."""
    max_round_robin_slices = 50_000
    saved_by_algorithm: dict[str, list[dict]] = {}
    for row in saved_rows:
        saved_by_algorithm.setdefault(row["algorithm"], []).append(row)
    playback = {}
    for algorithm in ("FCFS", "ROUND_ROBIN", "PRIORITY", "SJF"):
        results = saved_by_algorithm.get(algorithm, [])
        if algorithm == "ROUND_ROBIN":
            jobs = sorted(processes, key=lambda p: (p["arrival_time"], p["process_number"]))
            remaining = [p["burst_time"] for p in jobs]
            ready: deque[int] = deque()
            next_job = 0
            time = 0
            completed = 0
            segments = []
            while completed < len(jobs):
                if len(segments) >= max_round_robin_slices:
                    segments = None
                    break
                if not ready and next_job < len(jobs) and time < jobs[next_job]["arrival_time"]:
                    time = jobs[next_job]["arrival_time"]
                while next_job < len(jobs) and jobs[next_job]["arrival_time"] <= time:
                    ready.append(next_job)
                    next_job += 1
                index = ready.popleft()
                duration = min(quantum, remaining[index])
                segments.append({"process_number": jobs[index]["process_number"],
                                 "start": time, "end": time + duration})
                time += duration
                remaining[index] -= duration
                while next_job < len(jobs) and jobs[next_job]["arrival_time"] <= time:
                    ready.append(next_job)
                    next_job += 1
                if remaining[index]:
                    ready.append(index)
                else:
                    completed += 1
        else:
            segments = [{"process_number": row["process_number"],
                         "start": row["start_time"], "end": row["completion_time"]}
                        for row in results]
            segments.sort(key=lambda segment: (segment["start"], segment["process_number"]))
        playback[algorithm] = {
            "available": segments is not None,
            "duration": max((segment["end"] for segment in segments), default=0) if segments is not None else 0,
            "jobs": [{"process_number": p["process_number"], "arrival_time": p["arrival_time"]}
                     for p in processes],
            "segments": segments or [],
            "reason": ("This saved run needs too many Round Robin time slices to replay in the browser."
                       if segments is None else ""),
        }
    return playback


def build_process_state_events(schedules: dict) -> list[tuple[str, int, str, int, int]]:
    """Return per-process state transitions to persist with a simulation run."""
    events = []
    for algorithm, results in schedules.items():
        for result in results:
            transitions = [(0, 0, "NEW"),
                           (result["arrival_time"], 1, "READY")]
            if result["response_time"] > 0:
                # In this simulator WAITING means waiting for CPU service; I/O blocking is not modeled.
                transitions.append((result["arrival_time"], 2, "WAITING"))
            transitions.extend([
                (result["start_time"], 3, "RUNNING"),
                (result["completion_time"], 4, "TERMINATED"),
            ])
            transitions.sort(key=lambda event: (event[0], event[1]))
            for number, (simulation_time, _, state) in enumerate(transitions, start=1):
                events.append((algorithm, result["process_number"], state, simulation_time, number))
    return events


def calculate_schedules(processes: list[dict], quantum: int, lower_first: bool) -> dict:
    """Mirror the scheduling policies implemented by the C++ simulator."""
    fcfs = [dict(p) for p in processes]
    fcfs.sort(key=lambda p: p["arrival_time"])
    time = 0
    for p in fcfs:
        time = max(time, p["arrival_time"])
        p["start_time"] = time
        time += p["burst_time"]
        p["completion_time"] = time
        p["turnaround_time"] = time - p["arrival_time"]
        p["waiting_time"] = p["turnaround_time"] - p["burst_time"]

    rr = [dict(p) for p in processes]
    rr.sort(key=lambda p: p["arrival_time"])
    remaining = [p["burst_time"] for p in rr]
    ready: deque[int] = deque()
    next_process = 0
    time = 0
    completed = 0
    while completed < len(rr):
        if not ready and next_process < len(rr) and time < rr[next_process]["arrival_time"]:
            time = rr[next_process]["arrival_time"]
        while next_process < len(rr) and rr[next_process]["arrival_time"] <= time:
            ready.append(next_process)
            next_process += 1
        index = ready.popleft()
        p = rr[index]
        if "start_time" not in p:
            p["start_time"] = time
        duration = min(quantum, remaining[index])
        time += duration
        remaining[index] -= duration
        while next_process < len(rr) and rr[next_process]["arrival_time"] <= time:
            ready.append(next_process)
            next_process += 1
        if remaining[index]:
            ready.append(index)
        else:
            p["completion_time"] = time
            p["turnaround_time"] = time - p["arrival_time"]
            p["waiting_time"] = p["turnaround_time"] - p["burst_time"]
            completed += 1

    priority = [dict(p) for p in processes]
    done: set[int] = set()
    time = 0
    while len(done) < len(priority):
        eligible = [i for i, p in enumerate(priority)
                    if i not in done and p["arrival_time"] <= time]
        if not eligible:
            time = min(p["arrival_time"] for i, p in enumerate(priority) if i not in done)
            eligible = [i for i, p in enumerate(priority)
                        if i not in done and p["arrival_time"] <= time]
        def priority_key(i: int) -> tuple[int, int, int]:
            p = priority[i]
            return ((p["priority"] if lower_first else -p["priority"]),
                    p["arrival_time"], i)
        chosen = min(eligible, key=priority_key)
        p = priority[chosen]
        p["start_time"] = time
        time += p["burst_time"]
        p["completion_time"] = time
        p["turnaround_time"] = time - p["arrival_time"]
        p["waiting_time"] = p["turnaround_time"] - p["burst_time"]
        done.add(chosen)
    sjf = [dict(p) for p in processes]
    done = set()
    time = 0
    while len(done) < len(sjf):
        eligible = [i for i, p in enumerate(sjf)
                    if i not in done and p["arrival_time"] <= time]
        if not eligible:
            time = min(p["arrival_time"] for i, p in enumerate(sjf) if i not in done)
            eligible = [i for i, p in enumerate(sjf)
                        if i not in done and p["arrival_time"] <= time]
        chosen = min(eligible, key=lambda i: (sjf[i]["burst_time"],
                                              sjf[i]["arrival_time"], i))
        p = sjf[chosen]
        p["start_time"] = time
        time += p["burst_time"]
        p["completion_time"] = time
        p["turnaround_time"] = time - p["arrival_time"]
        p["waiting_time"] = p["turnaround_time"] - p["burst_time"]
        done.add(chosen)
    schedules = {"FCFS": fcfs, "ROUND_ROBIN": rr, "PRIORITY": priority, "SJF": sjf}
    for results in schedules.values():
        for process in results:
            process["response_time"] = process["start_time"] - process["arrival_time"]
    return schedules


def calculate_banker(available: list[int], allocation: list[list[int]],
                     maximum: list[list[int]]) -> tuple[bool, list[int]]:
    work = available[:]
    need = [[maximum[p][r] - allocation[p][r] for r in range(len(available))]
            for p in range(len(allocation))]
    finished = [False] * len(allocation)
    sequence: list[int] = []
    progress = True
    while len(sequence) < len(allocation) and progress:
        progress = False
        for p in range(len(allocation)):
            if finished[p] or any(need[p][r] > work[r] for r in range(len(available))):
                continue
            work = [work[r] + allocation[p][r] for r in range(len(available))]
            finished[p] = True
            sequence.append(p + 1)
            progress = True
    return len(sequence) == len(allocation), sequence


def read_form_data() -> dict:
    process_count = request.form.get("process_count", type=int)
    resource_count = request.form.get("resource_count", type=int)
    quantum = request.form.get("quantum", type=int)
    if process_count is None or not 1 <= process_count <= 20:
        raise ValueError("Choose between 1 and 20 processes.")
    if resource_count is None or not 1 <= resource_count <= 10:
        raise ValueError("Choose between 1 and 10 resource types.")
    if quantum is None or not 1 <= quantum <= 1_000_000:
        raise ValueError("Round Robin quantum must be from 1 to 1,000,000.")
    lower_first = request.form.get("priority_convention") == "LOWER_NUMBER_FIRST"

    processes = []
    for p in range(1, process_count + 1):
        arrival = request.form.get(f"arrival_{p}", type=int)
        burst = request.form.get(f"burst_{p}", type=int)
        priority = request.form.get(f"priority_{p}", type=int)
        if arrival is None or not 0 <= arrival <= 1_000_000:
            raise ValueError(f"P{p}: arrival time must be from 0 to 1,000,000.")
        if burst is None or not 1 <= burst <= 1_000_000:
            raise ValueError(f"P{p}: burst time must be from 1 to 1,000,000.")
        if priority is None or not 1 <= priority <= 100:
            raise ValueError(f"P{p}: priority must be from 1 to 100.")
        processes.append({"process_number": p, "arrival_time": arrival,
                          "burst_time": burst, "priority": priority})

    available = []
    allocation = [[0] * resource_count for _ in range(process_count)]
    maximum = [[0] * resource_count for _ in range(process_count)]
    total = []
    for r in range(1, resource_count + 1):
        units = request.form.get(f"available_{r}", type=int)
        if units is None or not 0 <= units <= 1000:
            raise ValueError(f"R{r}: available units must be from 0 to 1,000.")
        available.append(units)
    for p in range(1, process_count + 1):
        for r in range(1, resource_count + 1):
            allocated = request.form.get(f"allocated_{p}_{r}", type=int)
            claimed = request.form.get(f"maximum_{p}_{r}", type=int)
            if allocated is None or not 0 <= allocated <= 1000:
                raise ValueError(f"P{p}, R{r}: allocation must be from 0 to 1,000.")
            if claimed is None or not 0 <= claimed <= 10000:
                raise ValueError(f"P{p}, R{r}: maximum claim must be from 0 to 10,000.")
            allocation[p - 1][r - 1] = allocated
            maximum[p - 1][r - 1] = claimed
    for r in range(resource_count):
        resource_total = available[r] + sum(allocation[p][r] for p in range(process_count))
        total.append(resource_total)
        for p in range(process_count):
            if maximum[p][r] < allocation[p][r]:
                raise ValueError(f"P{p + 1}, R{r + 1}: maximum claim must be at least the allocation.")
            if maximum[p][r] > resource_total:
                raise ValueError(f"P{p + 1}, R{r + 1}: maximum claim cannot exceed total R{r + 1} units ({resource_total}).")
    return {"processes": processes, "process_count": process_count,
            "resource_count": resource_count, "quantum": quantum,
            "lower_first": lower_first, "available": available,
            "allocation": allocation, "maximum": maximum, "total": total}


def save_simulation(form_data: dict) -> int:
    processes = form_data["processes"]
    schedules = calculate_schedules(processes, form_data["quantum"], form_data["lower_first"])
    safe, sequence = calculate_banker(form_data["available"],
                                      form_data["allocation"], form_data["maximum"])
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        connection.start_transaction()
        with connection.cursor() as cursor:
            convention = "LOWER_NUMBER_FIRST" if form_data["lower_first"] else "HIGHER_NUMBER_FIRST"
            cursor.execute("INSERT INTO simulation_runs "
                           "(process_count, round_robin_quantum, priority_convention) VALUES (%s,%s,%s)",
                           (form_data["process_count"], form_data["quantum"], convention))
            run_id = cursor.lastrowid
            process_ids = []
            for p in processes:
                cursor.execute("INSERT INTO processes "
                               "(run_id, process_number, arrival_time, burst_time, priority) "
                               "VALUES (%s,%s,%s,%s,%s)",
                               (run_id, p["process_number"], p["arrival_time"], p["burst_time"], p["priority"]))
                process_ids.append(cursor.lastrowid)
            for algorithm, results in schedules.items():
                for result in results:
                    cursor.execute("INSERT INTO scheduling_results "
                                   "(run_id, process_id, algorithm, start_time, completion_time, response_time, waiting_time, turnaround_time) "
                                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                   (run_id, process_ids[result["process_number"] - 1], algorithm,
                                    result["start_time"], result["completion_time"],
                                    result["response_time"], result["waiting_time"],
                                    result["turnaround_time"]))
            for algorithm, process_number, state, simulation_time, transition_number in build_process_state_events(schedules):
                cursor.execute(
                    "INSERT INTO process_state_history "
                    "(run_id, process_id, algorithm, transition_number, process_state, simulation_time) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (run_id, process_ids[process_number - 1], algorithm,
                     transition_number, state, simulation_time),
                )
            for r in range(form_data["resource_count"]):
                cursor.execute("INSERT INTO resource_types "
                               "(run_id, resource_number, resource_name, total_units, available_units) "
                               "VALUES (%s,%s,%s,%s,%s)",
                               (run_id, r + 1, f"R{r + 1}", form_data["total"][r], form_data["available"][r]))
                resource_id = cursor.lastrowid
                for p in range(form_data["process_count"]):
                    cursor.execute("INSERT INTO resource_allocations "
                                   "(run_id, process_id, resource_id, allocated_units, maximum_units) "
                                   "VALUES (%s,%s,%s,%s,%s)",
                                   (run_id, process_ids[p], resource_id,
                                    form_data["allocation"][p][r], form_data["maximum"][p][r]))
            sequence_json = json.dumps([f"P{p}" for p in sequence]) if safe else None
            cursor.execute("INSERT INTO banker_checks (run_id, is_safe, safe_sequence) VALUES (%s,%s,%s)",
                           (run_id, int(safe), sequence_json))
            cursor.execute("INSERT INTO event_logs (run_id, event_type, message) "
                           "VALUES (%s,'SIMULATION_SAVED','Simulation created in the web dashboard')", (run_id,))
        connection.commit()
        return int(run_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_dashboard_data(run_id: int | None) -> dict:
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT metric_id, host_name, captured_at, cpu_percent, memory_percent, "
                "memory_used_bytes, memory_total_bytes FROM system_metrics "
                "ORDER BY metric_id DESC LIMIT 10"
            )
            system_metrics = cursor.fetchall()
            latest_system_metric = system_metrics[0] if system_metrics else None
            cursor.execute(
                "SELECT metric_id, captured_at, cpu_percent, memory_percent "
                "FROM system_metrics ORDER BY metric_id DESC LIMIT 12"
            )
            trend_samples = list(reversed(cursor.fetchall()))
            high_load_count = 0
            if trend_samples:
                cursor.execute(
                    "SELECT COUNT(*) AS high_load_count FROM system_load_predictions "
                    "WHERE high_load_predicted=TRUE AND metric_id BETWEEN %s AND %s",
                    (trend_samples[0]["metric_id"], trend_samples[-1]["metric_id"]),
                )
                high_load_count = int(cursor.fetchone()["high_load_count"])
            resource_trends = summarize_resource_trends(trend_samples, high_load_count)
            cursor.execute(
                "SELECT metric_id, captured_at, cpu_percent, memory_percent "
                "FROM system_metrics ORDER BY metric_id DESC LIMIT 100"
            )
            anomaly_samples = list(reversed(cursor.fetchall()))
            resource_anomalies = detect_resource_anomalies(anomaly_samples)
            cursor.execute(
                "SELECT lp.prediction_id, lp.predicted_at, lp.horizon_seconds, lp.sample_count, "
                "lp.cpu_predicted_percent, lp.memory_predicted_percent, lp.high_load_predicted, "
                "sm.host_name FROM system_load_predictions lp "
                "JOIN system_metrics sm ON sm.metric_id=lp.metric_id "
                "ORDER BY lp.prediction_id DESC LIMIT 1"
            )
            latest_prediction = cursor.fetchone()
            cursor.execute(
                "SELECT run_id, created_at, process_count, round_robin_quantum, "
                "priority_convention FROM simulation_runs ORDER BY run_id DESC"
            )
            runs = cursor.fetchall()
            if not runs:
                return {"runs": [], "run": None, "metrics": [], "processes": [],
                        "resources": [], "allocations": [], "banker": None,
                        "queue_playback": {},
                        "system_metrics": system_metrics,
                        "latest_system_metric": latest_system_metric,
                        "resource_trends": resource_trends,
                        "resource_anomalies": resource_anomalies,
                        "latest_prediction": latest_prediction,
                        "load_alert": bool(latest_prediction and latest_prediction["high_load_predicted"]),
                        "recommendation": low_priority_recommendation([], True)}

            selected = next((r for r in runs if r["run_id"] == run_id), runs[0])
            selected_id = selected["run_id"]

            cursor.execute(
                "SELECT sr.algorithm, AVG(sr.waiting_time) AS avg_waiting, "
                "AVG(sr.turnaround_time) AS avg_turnaround, "
                "AVG(sr.response_time) AS avg_response, "
                "SUM(p.burst_time) / NULLIF(MAX(sr.completion_time), 0) AS cpu_utilization "
                "FROM scheduling_results sr JOIN processes p "
                "ON p.run_id=sr.run_id AND p.process_id=sr.process_id "
                "WHERE sr.run_id=%s GROUP BY sr.algorithm "
                "ORDER BY FIELD(sr.algorithm,'FCFS','ROUND_ROBIN','PRIORITY','SJF')",
                (selected_id,),
            )
            metrics = cursor.fetchall()

            cursor.execute(
                "SELECT p.process_number, p.arrival_time, p.burst_time, p.priority, "
                "sr.algorithm, sr.start_time, sr.completion_time, sr.response_time, sr.waiting_time, "
                "sr.turnaround_time FROM processes p JOIN scheduling_results sr "
                "ON sr.run_id=p.run_id AND sr.process_id=p.process_id "
                "WHERE p.run_id=%s ORDER BY p.process_number, "
                "FIELD(sr.algorithm,'FCFS','ROUND_ROBIN','PRIORITY','SJF')",
                (selected_id,),
            )
            processes = cursor.fetchall()
            cursor.execute(
                "SELECT p.process_number, h.algorithm, h.process_state, h.simulation_time "
                "FROM process_state_history h JOIN processes p "
                "ON p.run_id=h.run_id AND p.process_id=h.process_id "
                "WHERE h.run_id=%s ORDER BY p.process_number, "
                "FIELD(h.algorithm,'FCFS','ROUND_ROBIN','PRIORITY','SJF'), h.transition_number",
                (selected_id,),
            )
            saved_states: dict[tuple[int, str], list[dict]] = {}
            for state_event in cursor.fetchall():
                saved_states.setdefault((state_event["process_number"], state_event["algorithm"]), []).append(state_event)
            for process in processes:
                events = saved_states.get((process["process_number"], process["algorithm"]), [])
                if events:
                    process["state_flow"] = " → ".join(
                        f"{event['process_state']} (t={event['simulation_time']})" for event in events)
                else:
                    # Older runs predate persisted state history.
                    states = ["NEW", "READY"]
                    if process["waiting_time"] > 0:
                        states.append("WAITING")
                    states.extend(["RUNNING", "TERMINATED"])
                    process["state_flow"] = " → ".join(states)

            cursor.execute(
                "SELECT resource_number, resource_name, total_units, available_units "
                "FROM resource_types WHERE run_id=%s ORDER BY resource_number",
                (selected_id,),
            )
            resources = cursor.fetchall()

            cursor.execute(
                "SELECT p.process_number, r.resource_name, a.allocated_units, a.maximum_units "
                "FROM resource_allocations a JOIN processes p "
                "ON p.run_id=a.run_id AND p.process_id=a.process_id "
                "JOIN resource_types r ON r.run_id=a.run_id AND r.resource_id=a.resource_id "
                "WHERE a.run_id=%s ORDER BY p.process_number, r.resource_number",
                (selected_id,),
            )
            allocations = cursor.fetchall()

            cursor.execute(
                "SELECT is_safe, safe_sequence, checked_at FROM banker_checks "
                "WHERE run_id=%s ORDER BY check_id DESC LIMIT 1",
                (selected_id,),
            )
            banker = cursor.fetchone()
            if banker and banker["safe_sequence"]:
                sequence = banker["safe_sequence"]
                banker["safe_sequence"] = json.loads(sequence) if isinstance(sequence, str) else sequence

            banker_steps = []
            if banker and banker["is_safe"] and banker["safe_sequence"]:
                simulated_available = [r["available_units"] for r in resources]
                allocation_by_process_resource = {
                    (a["process_number"], a["resource_name"]): a for a in allocations
                }
                for step_number, process_label in enumerate(banker["safe_sequence"], start=1):
                    process_number = int(process_label.removeprefix("P"))
                    resource_steps = []
                    for resource_index, resource in enumerate(resources):
                        allocation = allocation_by_process_resource.get(
                            (process_number, resource["resource_name"]),
                            {"allocated_units": 0, "maximum_units": 0},
                        )
                        before = simulated_available[resource_index]
                        need = allocation["maximum_units"] - allocation["allocated_units"]
                        released = allocation["allocated_units"]
                        after = before + released
                        resource_steps.append({
                            "resource_name": resource["resource_name"],
                            "available_before": before,
                            "need": need,
                            "released": released,
                            "available_after": after,
                        })
                        simulated_available[resource_index] = after
                    banker_steps.append({
                        "step": step_number,
                        "process": process_label,
                        "resources": resource_steps,
                    })

            unique_processes = {p["process_number"]: p for p in processes}
            process_inputs = [{key: unique_processes[number][key]
                               for key in ("process_number", "arrival_time", "burst_time", "priority")}
                              for number in sorted(unique_processes)]
            queue_playback = build_queue_playback(
                process_inputs, selected["round_robin_quantum"], processes)
            load_alert = bool(latest_prediction and latest_prediction["high_load_predicted"])
            recommendation = (low_priority_recommendation(
                list(unique_processes.values()),
                selected["priority_convention"] == "LOWER_NUMBER_FIRST") if load_alert else None)
            return {"runs": runs, "run": selected, "metrics": metrics,
                    "processes": processes, "resources": resources,
                    "queue_playback": queue_playback,
                    "allocations": allocations, "banker": banker,
                    "banker_steps": banker_steps,
                    "system_metrics": system_metrics,
                    "latest_system_metric": latest_system_metric,
                    "resource_trends": resource_trends,
                    "resource_anomalies": resource_anomalies,
                    "latest_prediction": latest_prediction,
                    "load_alert": load_alert,
                    "recommendation": recommendation}
    finally:
        connection.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        validate_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        connection = mysql.connector.connect(**DB_CONFIG)
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT user_id, username, password_hash, role FROM users "
                    "WHERE username=%s AND is_active=TRUE",
                    (username,),
                )
                user = cursor.fetchone()
        finally:
            connection.close()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Username or password is incorrect.", "error")
            return render_template("login.html"), 401
        session.clear()
        session["user_id"] = int(user["user_id"])
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/logout")
def logout():
    validate_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor(dictionary=True) as cursor:
            if request.method == "POST":
                validate_csrf()
                action = request.form.get("action")
                if action == "create":
                    username = request.form.get("username", "").strip()
                    password = request.form.get("password", "")
                    role = request.form.get("role", "user")
                    if (not 3 <= len(username) <= 80 or not username.isascii()
                            or not username.replace("_", "").replace("-", "").isalnum()):
                        flash("Username must be 3–80 characters and use letters, numbers, _ or -.", "error")
                    elif len(password) < 10:
                        flash("Use a password with at least 10 characters.", "error")
                    elif role not in {"admin", "user"}:
                        flash("Choose a valid account role.", "error")
                    else:
                        cursor.execute(
                            "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s)",
                            (username, generate_password_hash(password), role),
                        )
                        connection.commit()
                        flash(f"Account {username} created with the {role} role.", "success")
                elif action in {"set_role", "toggle_active"}:
                    target_id = request.form.get("user_id", type=int)
                    cursor.execute("SELECT user_id, role, is_active FROM users WHERE user_id=%s", (target_id,))
                    target = cursor.fetchone()
                    if not target:
                        flash("Account not found.", "error")
                    elif target["user_id"] == g.current_user["user_id"]:
                        flash("You cannot change your own role or account status.", "error")
                    elif action == "set_role":
                        new_role = request.form.get("role")
                        if new_role not in {"admin", "user"}:
                            flash("Choose a valid account role.", "error")
                        elif target["role"] == "admin" and target["is_active"] and new_role != "admin":
                            cursor.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND is_active=TRUE")
                            if cursor.fetchone()["n"] <= 1:
                                flash("Keep at least one active admin account.", "error")
                            else:
                                cursor.execute("UPDATE users SET role=%s WHERE user_id=%s", (new_role, target_id))
                                connection.commit()
                                flash("Account role updated.", "success")
                        else:
                            cursor.execute("UPDATE users SET role=%s WHERE user_id=%s", (new_role, target_id))
                            connection.commit()
                            flash("Account role updated.", "success")
                    elif target["role"] == "admin" and target["is_active"]:
                        cursor.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND is_active=TRUE")
                        if cursor.fetchone()["n"] <= 1:
                            flash("Keep at least one active admin account.", "error")
                        else:
                            cursor.execute("UPDATE users SET is_active=FALSE WHERE user_id=%s", (target_id,))
                            connection.commit()
                            flash("Account deactivated.", "success")
                    else:
                        cursor.execute("UPDATE users SET is_active=NOT is_active WHERE user_id=%s", (target_id,))
                        connection.commit()
                        flash("Account status updated.", "success")
                else:
                    flash("Unknown account action.", "error")
            cursor.execute("SELECT user_id, username, role, is_active, created_at FROM users ORDER BY user_id")
            users = cursor.fetchall()
    except mysql.connector.IntegrityError:
        connection.rollback()
        flash("That username is already in use.", "error")
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT user_id, username, role, is_active, created_at FROM users ORDER BY user_id")
            users = cursor.fetchall()
    finally:
        connection.close()
    return render_template("admin_users.html", users=users)


@app.get("/")
@login_required
def index():
    selected_id = request.args.get("run_id", type=int)
    try:
        data = fetch_dashboard_data(selected_id)
        error = None
    except mysql.connector.Error as exc:
        data = {"runs": [], "run": None, "metrics": [], "processes": [],
                "resources": [], "allocations": [], "banker": None,
                "queue_playback": {},
                "system_metrics": [], "latest_system_metric": None,
                "latest_prediction": None, "resource_trends": summarize_resource_trends([], 0),
                "resource_anomalies": {"sample_count": 0, "anomaly_count": 0,
                                       "status": "Waiting for readings"}}
        error = str(exc)
    return render_template("index.html", data=data, error=error)


@app.get("/reports/<int:run_id>.csv")
@login_required
def download_run_report(run_id: int):
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT run_id FROM simulation_runs WHERE run_id=%s", (run_id,))
            if not cursor.fetchone():
                abort(404)
            cursor.execute(
                "SELECT sr.algorithm, p.process_number, p.arrival_time, p.burst_time, p.priority, "
                "sr.start_time, sr.completion_time, sr.response_time, sr.waiting_time, sr.turnaround_time "
                "FROM scheduling_results sr JOIN processes p "
                "ON p.run_id=sr.run_id AND p.process_id=sr.process_id "
                "WHERE sr.run_id=%s "
                "ORDER BY FIELD(sr.algorithm,'FCFS','ROUND_ROBIN','PRIORITY','SJF'), p.process_number",
                (run_id,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["algorithm", "process_number", "arrival_time", "burst_time", "priority",
                     "start_time", "completion_time", "response_time", "waiting_time", "turnaround_time"])
    for row in rows:
        writer.writerow([row[key] for key in (
            "algorithm", "process_number", "arrival_time", "burst_time", "priority",
            "start_time", "completion_time", "response_time", "waiting_time", "turnaround_time")])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=intellisched-run-{run_id}.csv"
    return response


@app.get("/api/system-metrics")
@login_required
def latest_system_metrics():
    selected_run_id = request.args.get("run_id", type=int)
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        try:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT metric_id, host_name, captured_at, cpu_percent, memory_percent, "
                    "memory_used_bytes, memory_total_bytes FROM system_metrics "
                    "ORDER BY metric_id DESC LIMIT 1"
                )
                metric = cursor.fetchone()
                cursor.execute(
                    "SELECT lp.prediction_id, lp.predicted_at, lp.horizon_seconds, lp.sample_count, "
                    "lp.cpu_predicted_percent, lp.memory_predicted_percent, lp.high_load_predicted, "
                    "sm.host_name FROM system_load_predictions lp "
                    "JOIN system_metrics sm ON sm.metric_id=lp.metric_id "
                    "ORDER BY lp.prediction_id DESC LIMIT 1"
                )
                prediction = cursor.fetchone()
                recommendation = None
                if prediction and prediction["high_load_predicted"]:
                    if selected_run_id:
                        cursor.execute(
                            "SELECT priority_convention FROM simulation_runs WHERE run_id=%s",
                            (selected_run_id,),
                        )
                        selected = cursor.fetchone()
                        cursor.execute(
                            "SELECT process_number, priority FROM processes WHERE run_id=%s ORDER BY process_number",
                            (selected_run_id,),
                        )
                        recommendation = low_priority_recommendation(
                            cursor.fetchall(),
                            bool(selected and selected["priority_convention"] == "LOWER_NUMBER_FIRST"),
                        )
                    else:
                        recommendation = low_priority_recommendation([], True)
        finally:
            connection.close()
    except mysql.connector.Error as exc:
        return jsonify({"error": str(exc)}), 503
    if metric:
        metric["captured_at"] = metric["captured_at"].isoformat(sep=" ", timespec="seconds")
        metric["cpu_percent"] = float(metric["cpu_percent"])
        metric["memory_percent"] = float(metric["memory_percent"])
        metric["memory_used_bytes"] = int(metric["memory_used_bytes"])
        metric["memory_total_bytes"] = int(metric["memory_total_bytes"])
    if prediction:
        prediction["predicted_at"] = prediction["predicted_at"].isoformat(sep=" ", timespec="seconds")
        prediction["cpu_predicted_percent"] = float(prediction["cpu_predicted_percent"])
        prediction["memory_predicted_percent"] = float(prediction["memory_predicted_percent"])
        prediction["high_load_predicted"] = bool(prediction["high_load_predicted"])
        prediction["sample_count"] = int(prediction["sample_count"])
    return jsonify({"metric": metric, "prediction": prediction,
                    "recommendation": recommendation})


@app.route("/new", methods=["GET", "POST"])
@login_required
def new_simulation():
    if request.method == "GET":
        return render_template("new_simulation.html", process_count=3, resource_count=3)
    validate_csrf()
    try:
        form_data = read_form_data()
        run_id = save_simulation(form_data)
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("new_simulation.html",
                               process_count=request.form.get("process_count", 3, type=int) or 3,
                               resource_count=request.form.get("resource_count", 3, type=int) or 3), 400
    except mysql.connector.Error as exc:
        flash(f"MySQL could not save this run: {exc}", "error")
        return render_template("new_simulation.html",
                               process_count=request.form.get("process_count", 3, type=int) or 3,
                               resource_count=request.form.get("resource_count", 3, type=int) or 3), 500
    flash(f"Simulation saved as Run #{run_id}.", "success")
    return redirect(url_for("index", run_id=run_id))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
