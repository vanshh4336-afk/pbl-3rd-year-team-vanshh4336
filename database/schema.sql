CREATE DATABASE IF NOT EXISTS intellisched
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE intellisched;

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(80) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin', 'user') NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    process_count TINYINT UNSIGNED NOT NULL,
    round_robin_quantum INT UNSIGNED NOT NULL,
    priority_convention ENUM('LOWER_NUMBER_FIRST', 'HIGHER_NUMBER_FIRST') NOT NULL,
    PRIMARY KEY (run_id),
    CONSTRAINT chk_run_process_count CHECK (process_count BETWEEN 1 AND 20),
    CONSTRAINT chk_run_quantum CHECK (round_robin_quantum > 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS processes (
    process_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    process_number TINYINT UNSIGNED NOT NULL,
    arrival_time INT UNSIGNED NOT NULL,
    burst_time INT UNSIGNED NOT NULL,
    priority TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (process_id),
    UNIQUE KEY uq_process_run_number (run_id, process_number),
    UNIQUE KEY uq_process_run_id (run_id, process_id),
    CONSTRAINT fk_process_run FOREIGN KEY (run_id)
        REFERENCES simulation_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT chk_process_burst CHECK (burst_time > 0),
    CONSTRAINT chk_process_priority CHECK (priority BETWEEN 1 AND 100)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scheduling_results (
    result_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    process_id BIGINT UNSIGNED NOT NULL,
    algorithm ENUM('FCFS', 'ROUND_ROBIN', 'PRIORITY', 'SJF') NOT NULL,
    start_time INT UNSIGNED NOT NULL,
    completion_time INT UNSIGNED NOT NULL,
    response_time INT UNSIGNED NOT NULL,
    waiting_time INT UNSIGNED NOT NULL,
    turnaround_time INT UNSIGNED NOT NULL,
    PRIMARY KEY (result_id),
    UNIQUE KEY uq_result_run_process_algorithm (run_id, process_id, algorithm),
    CONSTRAINT fk_result_run FOREIGN KEY (run_id)
        REFERENCES simulation_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT fk_result_process FOREIGN KEY (run_id, process_id)
        REFERENCES processes (run_id, process_id) ON DELETE CASCADE,
    CONSTRAINT chk_result_completion CHECK (completion_time >= start_time),
    CONSTRAINT chk_result_response CHECK (response_time <= waiting_time),
    CONSTRAINT chk_result_turnaround CHECK (turnaround_time >= waiting_time)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS process_state_history (
    state_history_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    process_id BIGINT UNSIGNED NOT NULL,
    algorithm ENUM('FCFS', 'ROUND_ROBIN', 'PRIORITY', 'SJF') NOT NULL,
    transition_number TINYINT UNSIGNED NOT NULL,
    process_state ENUM('NEW', 'READY', 'RUNNING', 'WAITING', 'TERMINATED') NOT NULL,
    simulation_time INT UNSIGNED NOT NULL,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (state_history_id),
    UNIQUE KEY uq_state_transition (run_id, process_id, algorithm, transition_number),
    KEY idx_state_run_algorithm_time (run_id, algorithm, simulation_time),
    CONSTRAINT fk_state_process FOREIGN KEY (run_id, process_id)
        REFERENCES processes (run_id, process_id) ON DELETE CASCADE,
    CONSTRAINT chk_state_transition_number CHECK (transition_number > 0)
) ENGINE=InnoDB;

-- Backfill a basic state timeline for runs saved before state history existed.
INSERT IGNORE INTO process_state_history
    (run_id, process_id, algorithm, transition_number, process_state, simulation_time)
SELECT run_id, process_id, algorithm, transition_number, process_state, simulation_time
FROM (
    SELECT sr.run_id, sr.process_id, sr.algorithm, 1 AS transition_number,
           'NEW' AS process_state, 0 AS simulation_time
    FROM scheduling_results sr
    UNION ALL
    SELECT sr.run_id, sr.process_id, sr.algorithm, 2,
           'READY', p.arrival_time
    FROM scheduling_results sr JOIN processes p
      ON p.run_id=sr.run_id AND p.process_id=sr.process_id
    UNION ALL
    SELECT sr.run_id, sr.process_id, sr.algorithm, 3,
           'WAITING', p.arrival_time
    FROM scheduling_results sr JOIN processes p
      ON p.run_id=sr.run_id AND p.process_id=sr.process_id
    WHERE sr.start_time > p.arrival_time
    UNION ALL
    SELECT sr.run_id, sr.process_id, sr.algorithm, 4,
           'RUNNING', sr.start_time
    FROM scheduling_results sr
    UNION ALL
    SELECT sr.run_id, sr.process_id, sr.algorithm, 5,
           'TERMINATED', sr.completion_time
    FROM scheduling_results sr
) AS existing_state_events;

CREATE TABLE IF NOT EXISTS resource_types (
    resource_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    resource_number TINYINT UNSIGNED NOT NULL,
    resource_name VARCHAR(80) NOT NULL,
    total_units INT UNSIGNED NOT NULL,
    available_units INT UNSIGNED NOT NULL,
    PRIMARY KEY (resource_id),
    UNIQUE KEY uq_resource_run_number (run_id, resource_number),
    UNIQUE KEY uq_resource_run_id (run_id, resource_id),
    CONSTRAINT fk_resource_run FOREIGN KEY (run_id)
        REFERENCES simulation_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT chk_resource_available CHECK (available_units <= total_units)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resource_allocations (
    run_id BIGINT UNSIGNED NOT NULL,
    process_id BIGINT UNSIGNED NOT NULL,
    resource_id BIGINT UNSIGNED NOT NULL,
    allocated_units INT UNSIGNED NOT NULL,
    maximum_units INT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, process_id, resource_id),
    CONSTRAINT fk_allocation_process FOREIGN KEY (run_id, process_id)
        REFERENCES processes (run_id, process_id) ON DELETE CASCADE,
    CONSTRAINT fk_allocation_resource FOREIGN KEY (run_id, resource_id)
        REFERENCES resource_types (run_id, resource_id) ON DELETE CASCADE,
    CONSTRAINT chk_allocation_claim CHECK (maximum_units >= allocated_units)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS banker_checks (
    check_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    is_safe BOOLEAN NOT NULL,
    safe_sequence JSON NULL,
    checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (check_id),
    CONSTRAINT fk_banker_run FOREIGN KEY (run_id)
        REFERENCES simulation_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT chk_banker_sequence CHECK (
        (is_safe = TRUE AND safe_sequence IS NOT NULL) OR
        (is_safe = FALSE AND safe_sequence IS NULL)
    )
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS event_logs (
    log_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(60) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (log_id),
    KEY idx_event_run_created (run_id, created_at),
    CONSTRAINT fk_log_run FOREIGN KEY (run_id)
        REFERENCES simulation_runs (run_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS system_metrics (
    metric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    host_name VARCHAR(255) NOT NULL,
    captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cpu_percent DECIMAL(5,2) NOT NULL,
    memory_percent DECIMAL(5,2) NOT NULL,
    memory_used_bytes BIGINT UNSIGNED NOT NULL,
    memory_total_bytes BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (metric_id),
    KEY idx_system_metrics_host_time (host_name, captured_at),
    CONSTRAINT chk_system_metrics_cpu CHECK (cpu_percent BETWEEN 0 AND 100),
    CONSTRAINT chk_system_metrics_memory CHECK (memory_percent BETWEEN 0 AND 100)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS system_load_predictions (
    prediction_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    metric_id BIGINT UNSIGNED NOT NULL,
    predicted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    horizon_seconds SMALLINT UNSIGNED NOT NULL,
    sample_count TINYINT UNSIGNED NOT NULL,
    cpu_predicted_percent DECIMAL(5,2) NOT NULL,
    memory_predicted_percent DECIMAL(5,2) NOT NULL,
    high_load_predicted BOOLEAN NOT NULL,
    PRIMARY KEY (prediction_id),
    UNIQUE KEY uq_prediction_metric (metric_id),
    KEY idx_prediction_time (predicted_at),
    CONSTRAINT fk_prediction_metric FOREIGN KEY (metric_id)
        REFERENCES system_metrics (metric_id) ON DELETE CASCADE,
    CONSTRAINT chk_prediction_horizon CHECK (horizon_seconds > 0),
    CONSTRAINT chk_prediction_samples CHECK (sample_count >= 10),
    CONSTRAINT chk_prediction_cpu CHECK (cpu_predicted_percent BETWEEN 0 AND 100),
    CONSTRAINT chk_prediction_memory CHECK (memory_predicted_percent BETWEEN 0 AND 100)
) ENGINE=InnoDB;
