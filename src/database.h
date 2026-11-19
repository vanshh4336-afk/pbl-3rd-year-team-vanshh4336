#ifndef INTELLISCHED_DATABASE_H
#define INTELLISCHED_DATABASE_H

#include "models.h"
#include <string>

bool saveRun(const std::vector<Process>& inputs,
             const ScheduleResult& fcfs,
             const ScheduleResult& roundRobin,
             const ScheduleResult& priority,
             const ScheduleResult& sjf,
             const BankerResult& banker,
             int quantum,
             bool lowerNumberIsHigher);

#endif
