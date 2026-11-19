#include "database.h"
#include "models.h"

#include <algorithm>
#include <iomanip>
#include <iostream>
#include <queue>
#include <string>
#include <vector>

void calculateAverages(ScheduleResult& result) {
    if (result.processes.empty()) return;
    for (auto& process : result.processes) {
        process.responseTime = process.startTime - process.arrivalTime;
        result.averageResponse += process.responseTime;
        result.averageWaiting += process.waitingTime;
        result.averageTurnaround += process.turnaroundTime;
    }
    result.averageResponse /= result.processes.size();
    result.averageWaiting /= result.processes.size();
    result.averageTurnaround /= result.processes.size();
}

ScheduleResult runFcfs(const std::vector<Process>& input) {
    ScheduleResult result{input};
    std::stable_sort(result.processes.begin(), result.processes.end(),
                     [](const Process& a, const Process& b) {
                         return a.arrivalTime < b.arrivalTime;
                     });
    int currentTime = 0;
    for (auto& process : result.processes) {
        currentTime = std::max(currentTime, process.arrivalTime);
        process.startTime = currentTime;
        currentTime += process.burstTime;
        process.completionTime = currentTime;
        process.turnaroundTime = process.completionTime - process.arrivalTime;
        process.waitingTime = process.turnaroundTime - process.burstTime;
    }
    calculateAverages(result);
    return result;
}

ScheduleResult runRoundRobin(const std::vector<Process>& input, int quantum) {
    ScheduleResult result{input};
    std::stable_sort(result.processes.begin(), result.processes.end(),
                     [](const Process& a, const Process& b) {
                         return a.arrivalTime < b.arrivalTime;
                     });
    std::vector<int> remaining;
    for (const auto& process : result.processes) remaining.push_back(process.burstTime);

    std::queue<std::size_t> ready;
    std::size_t next = 0;
    int currentTime = 0;
    int completed = 0;
    while (completed < static_cast<int>(result.processes.size())) {
        if (ready.empty() && next < result.processes.size() &&
            currentTime < result.processes[next].arrivalTime) {
            currentTime = result.processes[next].arrivalTime;
        }
        while (next < result.processes.size() &&
               result.processes[next].arrivalTime <= currentTime) ready.push(next++);
        const std::size_t index = ready.front();
        ready.pop();
        Process& process = result.processes[index];
        if (process.startTime == -1) process.startTime = currentTime;
        const int runTime = std::min(quantum, remaining[index]);
        currentTime += runTime;
        remaining[index] -= runTime;
        while (next < result.processes.size() &&
               result.processes[next].arrivalTime <= currentTime) ready.push(next++);
        if (remaining[index] > 0) {
            ready.push(index);
        } else {
            process.completionTime = currentTime;
            process.turnaroundTime = process.completionTime - process.arrivalTime;
            process.waitingTime = process.turnaroundTime - process.burstTime;
            ++completed;
        }
    }
    calculateAverages(result);
    return result;
}

ScheduleResult runPriority(const std::vector<Process>& input, bool lowerNumberIsHigher) {
    ScheduleResult result{input};
    std::vector<bool> completed(result.processes.size(), false);
    int currentTime = 0;
    std::size_t completedCount = 0;
    while (completedCount < result.processes.size()) {
        std::size_t selected = result.processes.size();
        for (std::size_t i = 0; i < result.processes.size(); ++i) {
            if (completed[i] || result.processes[i].arrivalTime > currentTime) continue;
            if (selected == result.processes.size()) {
                selected = i;
                continue;
            }
            const Process& candidate = result.processes[i];
            const Process& best = result.processes[selected];
            const bool betterPriority = lowerNumberIsHigher
                ? candidate.priority < best.priority : candidate.priority > best.priority;
            if (betterPriority || (candidate.priority == best.priority &&
                                   candidate.arrivalTime < best.arrivalTime)) selected = i;
        }
        if (selected == result.processes.size()) {
            int nextArrival = -1;
            for (std::size_t i = 0; i < result.processes.size(); ++i) {
                if (!completed[i] && (nextArrival == -1 ||
                    result.processes[i].arrivalTime < nextArrival)) nextArrival = result.processes[i].arrivalTime;
            }
            currentTime = nextArrival;
            continue;
        }
        Process& process = result.processes[selected];
        process.startTime = currentTime;
        currentTime += process.burstTime;
        process.completionTime = currentTime;
        process.turnaroundTime = process.completionTime - process.arrivalTime;
        process.waitingTime = process.turnaroundTime - process.burstTime;
        completed[selected] = true;
        ++completedCount;
    }
    calculateAverages(result);
    return result;
}

ScheduleResult runSjf(const std::vector<Process>& input) {
    ScheduleResult result{input};
    std::vector<bool> completed(result.processes.size(), false);
    int currentTime = 0;
    std::size_t completedCount = 0;
    while (completedCount < result.processes.size()) {
        std::size_t selected = result.processes.size();
        for (std::size_t i = 0; i < result.processes.size(); ++i) {
            const Process& candidate = result.processes[i];
            if (completed[i] || candidate.arrivalTime > currentTime) continue;
            if (selected == result.processes.size() ||
                candidate.burstTime < result.processes[selected].burstTime ||
                (candidate.burstTime == result.processes[selected].burstTime &&
                 candidate.arrivalTime < result.processes[selected].arrivalTime)) {
                selected = i;
            }
        }
        if (selected == result.processes.size()) {
            int nextArrival = -1;
            for (std::size_t i = 0; i < result.processes.size(); ++i) {
                if (!completed[i] && (nextArrival == -1 ||
                    result.processes[i].arrivalTime < nextArrival)) {
                    nextArrival = result.processes[i].arrivalTime;
                }
            }
            currentTime = nextArrival;
            continue;
        }

        Process& process = result.processes[selected];
        process.startTime = currentTime;
        currentTime += process.burstTime;
        process.completionTime = currentTime;
        process.turnaroundTime = process.completionTime - process.arrivalTime;
        process.waitingTime = process.turnaroundTime - process.burstTime;
        completed[selected] = true;
        ++completedCount;
    }
    calculateAverages(result);
    return result;
}

void printResults(const std::string& title, const ScheduleResult& result) {
    std::cout << "\n" << title << "\n";
    std::cout << std::left << std::setw(10) << "Process" << std::setw(10) << "Arrival"
              << std::setw(10) << "Burst" << std::setw(10) << "Priority"
              << std::setw(10) << "Start" << std::setw(12) << "Complete"
              << std::setw(10) << "Response" << std::setw(10) << "Waiting"
              << "Turnaround\n";
    for (const auto& process : result.processes) {
        std::cout << std::left << std::setw(10) << ("P" + std::to_string(process.id))
                  << std::setw(10) << process.arrivalTime << std::setw(10) << process.burstTime
                  << std::setw(10) << process.priority << std::setw(10) << process.startTime
                  << std::setw(12) << process.completionTime << std::setw(10) << process.responseTime
                  << std::setw(10) << process.waitingTime
                  << process.turnaroundTime << '\n';
    }
    std::cout << std::fixed << std::setprecision(2)
              << "Average response time: " << result.averageResponse
              << "\nAverage waiting time: " << result.averageWaiting
              << "\nAverage turnaround time: " << result.averageTurnaround << '\n';
}

void printStateFlows(const std::string& title, const ScheduleResult& result, int quantum) {
    std::cout << "Process state flow (WAITING means waiting for CPU in this simulation):\n";
    const bool roundRobin = title.find("Round Robin") != std::string::npos;
    for (const auto& process : result.processes) {
        std::cout << "  P" << process.id << ": NEW -> READY";
        if (roundRobin) {
            const int slices = (process.burstTime + quantum - 1) / quantum;
            for (int slice = 0; slice < slices; ++slice) {
                std::cout << " -> RUNNING";
                if (slice + 1 < slices) std::cout << " -> WAITING -> READY";
            }
        } else {
            if (process.waitingTime > 0) std::cout << " -> WAITING";
            std::cout << " -> RUNNING";
        }
        std::cout << " -> TERMINATED\n";
    }
}

int readInteger(const std::string& prompt, int minimum, int maximum) {
    int value;
    while (true) {
        std::cout << prompt;
        if (std::cin >> value && value >= minimum && value <= maximum) return value;
        std::cout << "Enter a whole number from " << minimum << " to " << maximum << ".\n";
        std::cin.clear();
        std::cin.ignore(10000, '\n');
    }
}

void showProcesses(const std::vector<Process>& processes) {
    std::cout << "\nProcess monitor (before scheduling; state = NEW)\n";
    std::cout << std::left << std::setw(10) << "Process" << std::setw(12) << "Arrival"
              << std::setw(10) << "Burst" << std::setw(10) << "Priority" << "State\n";
    for (const auto& process : processes) {
        std::cout << std::left << std::setw(10) << ("P" + std::to_string(process.id))
                  << std::setw(12) << process.arrivalTime << std::setw(10) << process.burstTime
                  << std::setw(10) << process.priority << "NEW\n";
    }
}

void manageProcesses(std::vector<Process>& processes) {
    while (true) {
        std::cout << "\nProcess manager: 1 = add, 2 = update, 3 = terminate, "
                     "4 = monitor/list, 5 = continue to scheduling\n";
        const int choice = readInteger("Choose an action (1-5): ", 1, 5);
        if (choice == 5) return;
        if (choice == 4) {
            showProcesses(processes);
            continue;
        }
        if (choice == 1) {
            if (processes.size() >= 20) {
                std::cout << "The maximum is 20 processes.\n";
                continue;
            }
            const int id = static_cast<int>(processes.size()) + 1;
            std::cout << "\nNew process P" << id << "\n";
            const int arrival = readInteger("  Arrival time (0 or more): ", 0, 1000000);
            const int burst = readInteger("  Burst time (at least 1): ", 1, 1000000);
            const int priority = readInteger("  Priority (1-100): ", 1, 100);
            processes.push_back({id, arrival, burst, priority});
            std::cout << "Added P" << id << ".\n";
            continue;
        }

        showProcesses(processes);
        const int selected = readInteger("Select process number: ", 1,
                                         static_cast<int>(processes.size()));
        if (choice == 2) {
            Process& process = processes[static_cast<std::size_t>(selected - 1)];
            std::cout << "Update P" << selected << "\n";
            process.arrivalTime = readInteger("  Arrival time (0 or more): ", 0, 1000000);
            process.burstTime = readInteger("  Burst time (at least 1): ", 1, 1000000);
            process.priority = readInteger("  Priority (1-100): ", 1, 100);
            std::cout << "Updated P" << selected << ".\n";
        } else {
            if (processes.size() == 1) {
                std::cout << "At least one process is required; P" << selected
                          << " was not terminated.\n";
                continue;
            }
            processes.erase(processes.begin() + selected - 1);
            for (std::size_t i = 0; i < processes.size(); ++i) {
                processes[i].id = static_cast<int>(i) + 1;
            }
            std::cout << "Terminated and removed P" << selected
                      << " from this unscheduled run.\n";
        }
    }
}

BankerResult runBankersAlgorithm(int processCount) {
    BankerResult result;
    std::cout << "\nResource allocation and Banker's safety check\n";
    const int resourceCount = readInteger("Number of resource types (1-10): ", 1, 10);
    std::vector<int> available(resourceCount);
    std::vector<int> total(resourceCount);
    std::vector<std::vector<int>> allocation(processCount,
                                              std::vector<int>(resourceCount));
    std::vector<std::vector<int>> maximum(processCount,
                                           std::vector<int>(resourceCount));
    std::vector<std::vector<int>> need(processCount,
                                        std::vector<int>(resourceCount));

    std::cout << "Enter the currently available units for each resource type.\n";
    for (int r = 0; r < resourceCount; ++r) {
        available[r] = readInteger("  R" + std::to_string(r + 1) + " available: ", 0, 1000);
    }

    std::cout << "\nEnter current allocation for each process/resource pair.\n";
    for (int p = 0; p < processCount; ++p) {
        std::cout << "  Process P" << p + 1 << ":\n";
        for (int r = 0; r < resourceCount; ++r) {
            allocation[p][r] = readInteger("    R" + std::to_string(r + 1) + " allocated: ", 0, 1000);
            total[r] += allocation[p][r];
        }
    }
    for (int r = 0; r < resourceCount; ++r) total[r] += available[r];

    std::cout << "\nEnter each process's maximum total claim (must be at least its allocation).\n";
    for (int p = 0; p < processCount; ++p) {
        std::cout << "  Process P" << p + 1 << ":\n";
        for (int r = 0; r < resourceCount; ++r) {
            maximum[p][r] = readInteger("    R" + std::to_string(r + 1) + " maximum: ",
                                       allocation[p][r], total[r]);
            need[p][r] = maximum[p][r] - allocation[p][r];
        }
    }

    std::cout << "\nNeed matrix (maximum - allocation):\n";
    std::cout << std::left << std::setw(10) << "Process";
    for (int r = 0; r < resourceCount; ++r) std::cout << std::setw(8) << ("R" + std::to_string(r + 1));
    std::cout << '\n';
    for (int p = 0; p < processCount; ++p) {
        std::cout << std::left << std::setw(10) << ("P" + std::to_string(p + 1));
        for (int r = 0; r < resourceCount; ++r) std::cout << std::setw(8) << need[p][r];
        std::cout << '\n';
    }

    std::vector<int> work = available;
    std::vector<bool> finished(processCount, false);
    std::vector<int> safeSequence;
    bool progress = true;
    while (safeSequence.size() < static_cast<std::size_t>(processCount) && progress) {
        progress = false;
        for (int p = 0; p < processCount; ++p) {
            if (finished[p]) continue;
            bool canFinish = true;
            for (int r = 0; r < resourceCount; ++r) {
                if (need[p][r] > work[r]) {
                    canFinish = false;
                    break;
                }
            }
            if (canFinish) {
                for (int r = 0; r < resourceCount; ++r) work[r] += allocation[p][r];
                finished[p] = true;
                safeSequence.push_back(p + 1);
                progress = true;
            }
        }
    }

    if (safeSequence.size() == static_cast<std::size_t>(processCount)) {
        result.isSafe = true;
        result.safeSequence = safeSequence;
        std::cout << "System is SAFE. Safe sequence: ";
        for (std::size_t i = 0; i < safeSequence.size(); ++i) {
            if (i > 0) std::cout << " -> ";
            std::cout << "P" << safeSequence[i];
        }
        std::cout << '\n';
    } else {
        std::cout << "System is UNSAFE with the current available resources. No safe sequence exists.\n";
    }

    if (result.isSafe) {
        std::cout << "\nBanker's resource-release simulation "
                     "(temporary Work values; saved allocations are unchanged)\n";
        std::vector<int> simulatedAvailable = available;
        for (std::size_t step = 0; step < safeSequence.size(); ++step) {
            const int processIndex = safeSequence[step] - 1;
            std::cout << "  Step " << step + 1 << ", P" << safeSequence[step] << ": ";
            for (int r = 0; r < resourceCount; ++r) {
                if (r > 0) std::cout << " | ";
                std::cout << "R" << r + 1 << " available " << simulatedAvailable[r]
                          << ", need " << need[processIndex][r]
                          << ", release " << allocation[processIndex][r];
                simulatedAvailable[r] += allocation[processIndex][r];
                std::cout << ", after " << simulatedAvailable[r];
            }
            std::cout << '\n';
        }
    }
    result.available = available;
    result.total = total;
    result.allocation = allocation;
    result.maximum = maximum;
    result.need = need;
    return result;
}

int main() {
    std::cout << "IntelliSched - CPU Scheduling and Resource Management\n";
    const int processCount = readInteger("Initial number of processes (1-20): ", 1, 20);
    std::vector<Process> processes;
    processes.reserve(processCount);
    for (int i = 0; i < processCount; ++i) {
        std::cout << "\nProcess P" << i + 1 << "\n";
        const int arrival = readInteger("  Arrival time (0 or more): ", 0, 1000000);
        const int burst = readInteger("  Burst time (at least 1): ", 1, 1000000);
        const int priority = readInteger("  Priority (1-100; meaning selected below): ", 1, 100);
        processes.push_back({i + 1, arrival, burst, priority});
    }

    manageProcesses(processes);

    const int quantum = readInteger("\nRound Robin time quantum (1-1000000): ", 1, 1000000);
    std::cout << "Priority convention: 1 = lower number is higher priority; 2 = higher number is higher priority.\n";
    const int convention = readInteger("Choose convention (1 or 2): ", 1, 2);
    const bool lowerNumberIsHigher = convention == 1;

    const ScheduleResult fcfs = runFcfs(processes);
    const ScheduleResult roundRobin = runRoundRobin(processes, quantum);
    const ScheduleResult priority = runPriority(processes, lowerNumberIsHigher);
    const ScheduleResult sjf = runSjf(processes);
    printResults("First Come, First Served (FCFS)", fcfs);
    printStateFlows("First Come, First Served (FCFS)", fcfs, quantum);
    printResults("Round Robin (time quantum = " + std::to_string(quantum) + ")", roundRobin);
    printStateFlows("Round Robin", roundRobin, quantum);
    printResults(std::string("Non-preemptive Priority (") +
                 (lowerNumberIsHigher ? "lower number = higher priority)" :
                                        "higher number = higher priority)"), priority);
    printStateFlows("Non-preemptive Priority", priority, quantum);
    printResults("Non-preemptive Shortest Job First (SJF)", sjf);
    printStateFlows("Non-preemptive Shortest Job First (SJF)", sjf, quantum);

    const BankerResult banker = runBankersAlgorithm(static_cast<int>(processes.size()));
    const int saveChoice = readInteger("\nSave this run to MySQL? (1 = yes, 2 = no): ", 1, 2);
    if (saveChoice == 1) {
        saveRun(processes, fcfs, roundRobin, priority, sjf, banker, quantum,
                lowerNumberIsHigher);
    }
    return 0;
}
