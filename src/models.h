#ifndef INTELLISCHED_MODELS_H
#define INTELLISCHED_MODELS_H

#include <vector>

struct Process {
    int id;
    int arrivalTime;
    int burstTime;
    int priority;
    int startTime = -1;
    int completionTime = 0;
    int responseTime = 0;
    int waitingTime = 0;
    int turnaroundTime = 0;
};

struct ScheduleResult {
    std::vector<Process> processes;
    double averageWaiting = 0.0;
    double averageTurnaround = 0.0;
    double averageResponse = 0.0;
};

struct BankerResult {
    std::vector<int> available;
    std::vector<int> total;
    std::vector<std::vector<int>> allocation;
    std::vector<std::vector<int>> maximum;
    std::vector<std::vector<int>> need;
    bool isSafe = false;
    std::vector<int> safeSequence;
};

#endif
