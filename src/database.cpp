#include "database.h"

#include <mysql.h>
#include <algorithm>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {
constexpr const char* kDbUsername = "root";
constexpr const char* kDbPassword = "oracle";

bool execute(MYSQL* connection, const std::string& sql) {
    if (mysql_query(connection, sql.c_str()) == 0) return true;
    std::cerr << "MySQL error: " << mysql_error(connection) << '\n';
    return false;
}

std::string number(int value) {
    return std::to_string(value);
}

bool insertScheduleResults(MYSQL* connection,
                           unsigned long long runId,
                           const std::vector<unsigned long long>& processIds,
                           const ScheduleResult& result,
                           const std::string& algorithm) {
    for (const auto& process : result.processes) {
        const auto processId = processIds.at(static_cast<std::size_t>(process.id - 1));
        const std::string sql =
            "INSERT INTO scheduling_results "
            "(run_id, process_id, algorithm, start_time, completion_time, response_time, waiting_time, turnaround_time) VALUES (" +
            std::to_string(runId) + ", " + std::to_string(processId) + ", '" + algorithm + "', " +
            number(process.startTime) + ", " + number(process.completionTime) + ", " +
            number(process.responseTime) + ", " + number(process.waitingTime) + ", " +
            number(process.turnaroundTime) + ")";
        if (!execute(connection, sql)) return false;
    }
    return true;
}

bool insertProcessStateHistory(MYSQL* connection,
                               unsigned long long runId,
                               const std::vector<unsigned long long>& processIds,
                               const ScheduleResult& result,
                               const std::string& algorithm) {
    for (const auto& process : result.processes) {
        const auto processId = processIds.at(static_cast<std::size_t>(process.id - 1));
        std::vector<std::pair<int, std::string>> transitions{
            {0, "NEW"}, {process.arrivalTime, "READY"}
        };
        if (process.responseTime > 0) transitions.push_back({process.arrivalTime, "WAITING"});
        transitions.push_back({process.startTime, "RUNNING"});
        transitions.push_back({process.completionTime, "TERMINATED"});
        std::stable_sort(transitions.begin(), transitions.end(),
                         [](const auto& left, const auto& right) { return left.first < right.first; });
        unsigned int transitionNumber = 0;
        for (const auto& transition : transitions) {
            const std::string sql =
                "INSERT INTO process_state_history "
                "(run_id, process_id, algorithm, transition_number, process_state, simulation_time) VALUES (" +
                std::to_string(runId) + ", " + std::to_string(processId) + ", '" + algorithm + "', " +
                std::to_string(++transitionNumber) + ", '" + transition.second + "', " +
                number(transition.first) + ")";
            if (!execute(connection, sql)) return false;
        }
    }
    return true;
}
}

bool saveRun(const std::vector<Process>& inputs,
             const ScheduleResult& fcfs,
             const ScheduleResult& roundRobin,
             const ScheduleResult& priority,
             const ScheduleResult& sjf,
             const BankerResult& banker,
             int quantum,
             bool lowerNumberIsHigher) {
    MYSQL* connection = mysql_init(nullptr);
    if (connection == nullptr) {
        std::cerr << "Could not initialize MySQL client.\n";
        return false;
    }
    const char* charset = "utf8mb4";
    mysql_options(connection, MYSQL_SET_CHARSET_NAME, charset);
    if (mysql_real_connect(connection, "127.0.0.1", kDbUsername, kDbPassword,
                           "intellisched", 3306, nullptr, 0) == nullptr) {
        std::cerr << "Could not connect to MySQL: " << mysql_error(connection) << '\n';
        mysql_close(connection);
        return false;
    }

    bool ok = execute(connection, "START TRANSACTION");
    const std::string convention = lowerNumberIsHigher
        ? "LOWER_NUMBER_FIRST" : "HIGHER_NUMBER_FIRST";
    if (ok) {
        ok = execute(connection,
            "INSERT INTO simulation_runs (process_count, round_robin_quantum, priority_convention) VALUES (" +
            std::to_string(inputs.size()) + ", " + number(quantum) + ", '" + convention + "')");
    }

    const unsigned long long runId = ok ? mysql_insert_id(connection) : 0;
    std::vector<unsigned long long> processIds;
    processIds.reserve(inputs.size());
    for (const auto& process : inputs) {
        if (!ok) break;
        ok = execute(connection,
            "INSERT INTO processes (run_id, process_number, arrival_time, burst_time, priority) VALUES (" +
            std::to_string(runId) + ", " + number(process.id) + ", " +
            number(process.arrivalTime) + ", " + number(process.burstTime) + ", " +
            number(process.priority) + ")");
        if (ok) processIds.push_back(mysql_insert_id(connection));
    }

    if (ok) ok = insertScheduleResults(connection, runId, processIds, fcfs, "FCFS");
    if (ok) ok = insertScheduleResults(connection, runId, processIds, roundRobin, "ROUND_ROBIN");
    if (ok) ok = insertScheduleResults(connection, runId, processIds, priority, "PRIORITY");
    if (ok) ok = insertScheduleResults(connection, runId, processIds, sjf, "SJF");
    if (ok) ok = insertProcessStateHistory(connection, runId, processIds, fcfs, "FCFS");
    if (ok) ok = insertProcessStateHistory(connection, runId, processIds, roundRobin, "ROUND_ROBIN");
    if (ok) ok = insertProcessStateHistory(connection, runId, processIds, priority, "PRIORITY");
    if (ok) ok = insertProcessStateHistory(connection, runId, processIds, sjf, "SJF");

    for (std::size_t r = 0; ok && r < banker.available.size(); ++r) {
        const std::string resourceName = "R" + std::to_string(r + 1);
        ok = execute(connection,
            "INSERT INTO resource_types (run_id, resource_number, resource_name, total_units, available_units) VALUES (" +
            std::to_string(runId) + ", " + std::to_string(r + 1) + ", '" + resourceName + "', " +
            number(banker.total[r]) + ", " + number(banker.available[r]) + ")");
        const unsigned long long resourceId = ok ? mysql_insert_id(connection) : 0;
        for (std::size_t p = 0; ok && p < inputs.size(); ++p) {
            ok = execute(connection,
                "INSERT INTO resource_allocations (run_id, process_id, resource_id, allocated_units, maximum_units) VALUES (" +
                std::to_string(runId) + ", " + std::to_string(processIds[p]) + ", " +
                std::to_string(resourceId) + ", " + number(banker.allocation[p][r]) + ", " +
                number(banker.maximum[p][r]) + ")");
        }
    }

    if (ok) {
        std::string sequence = "[";
        for (std::size_t i = 0; i < banker.safeSequence.size(); ++i) {
            if (i > 0) sequence += ",";
            sequence += "\"P" + std::to_string(banker.safeSequence[i]) + "\"";
        }
        sequence += "]";
        const std::string sequenceSql = banker.isSafe ? "'" + sequence + "'" : "NULL";
        ok = execute(connection,
            "INSERT INTO banker_checks (run_id, is_safe, safe_sequence) VALUES (" +
            std::to_string(runId) + ", " + (banker.isSafe ? "1" : "0") + ", " + sequenceSql + ")");
    }
    if (ok) {
        ok = execute(connection,
            "INSERT INTO event_logs (run_id, event_type, message) VALUES (" +
            std::to_string(runId) + ", 'SIMULATION_SAVED', 'Simulation inputs and results saved')");
    }
    if (ok) {
        ok = execute(connection, "COMMIT");
    } else {
        mysql_query(connection, "ROLLBACK");
    }

    if (ok) std::cout << "Saved simulation run " << runId << " to database intellisched.\n";
    mysql_close(connection);
    return ok;
}
