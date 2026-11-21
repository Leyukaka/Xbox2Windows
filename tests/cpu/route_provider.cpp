// Provider for the exe_route fixture. route_value reads ROUTE_INPUT from the process
// environment, so the same EXE takes a different path for each input. It writes the arguments
// it received and its result to stdout, which the test compares between builds.
#include <cstdint>
#include <cstdio>
#include <cstring>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

using Word = std::uint64_t;

extern "C" __declspec(dllexport) Word route_value(Word first, Word second, Word* trace) {
    char input[16] = {};
    const DWORD length = GetEnvironmentVariableA("ROUTE_INPUT", input, sizeof(input));
    const bool high = length > 0 && length < sizeof(input) && std::strcmp(input, "high") == 0;
    const Word result = first + second + (high ? 200 : 0);
    char line[128];
    const int size = std::snprintf(line, sizeof(line), "route_value %llu %llu %p -> %llu\n",
                                   static_cast<unsigned long long>(first),
                                   static_cast<unsigned long long>(second), static_cast<void*>(trace),
                                   static_cast<unsigned long long>(result));
    DWORD written = 0;
    if (size > 0) WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, static_cast<DWORD>(size), &written, nullptr);
    return result;
}
