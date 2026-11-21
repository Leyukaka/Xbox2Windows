// Records initialization calls from tls_route images. It is loaded before and unloaded after
// the images it observes, so detach calls remain visible after the image is gone. Each call
// records the calling thread, so thread notifications can be attributed to one thread.
#include <windows.h>

#include <cstddef>
#include <cstdint>

struct Call {
    std::uint32_t caller;
    std::uint32_t reason;
    std::uint64_t module;
    std::uint64_t reserved;
    std::uint32_t thread;
    std::uint32_t padding;
};

static Call calls[64];
static volatile LONG count;

// One-shot hook run after the next recorded DLL_THREAD_ATTACH, on that thread. Tests use it to call
// pe_map APIs from inside a guest TLS callback without any Python on that path.
using Hook = void (*)(void*);
static void* volatile pending_hook;
static void* volatile pending_argument;
static volatile DWORD pending_thread;

extern "C" __declspec(dllexport) void sink_record(void* module, std::uint32_t reason, void* reserved,
                                                  std::uint32_t caller) {
    const LONG index = InterlockedIncrement(&count) - 1;
    if (index < static_cast<LONG>(sizeof(calls) / sizeof(calls[0])))
        calls[index] = {caller, reason, reinterpret_cast<std::uint64_t>(module),
                        reinterpret_cast<std::uint64_t>(reserved), GetCurrentThreadId(), 0};
    // Only the armed thread consumes the hook, so an unrelated thread (a pool worker) cannot take it.
    if (reason == DLL_THREAD_ATTACH && pending_thread == GetCurrentThreadId()) {
        if (auto hook = reinterpret_cast<Hook>(InterlockedExchangePointer(&pending_hook, nullptr))) {
            hook(pending_argument);  // written before the hook was published
        }
    }
}

extern "C" __declspec(dllexport) void sink_on_thread_attach(Hook hook, void* argument, DWORD thread) {
    pending_argument = argument;
    pending_thread = thread;
    InterlockedExchangePointer(&pending_hook, reinterpret_cast<void*>(hook));
}

extern "C" __declspec(dllexport) std::size_t sink_read(Call* out, std::size_t capacity) {
    constexpr std::size_t slots = sizeof(calls) / sizeof(calls[0]);
    const std::size_t recorded = static_cast<std::size_t>(count);
    const std::size_t stored = recorded < slots ? recorded : slots;
    for (std::size_t i = 0; i < stored && i < capacity; ++i) out[i] = calls[i];
    return stored;
}

extern "C" __declspec(dllexport) void sink_clear() { InterlockedExchange(&count, 0); }
