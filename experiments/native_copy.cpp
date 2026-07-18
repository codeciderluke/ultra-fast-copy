// Minimal native multithreaded copier, to find the real ceiling on this machine.
//
// Deliberately does the least work possible: enumerate, pre-create directories,
// then CopyFileW from a fixed thread pool. No verification, no partial files,
// no progress, no retry. If a C++ rewrite of Ultra Fast Copy could beat
// Robocopy, this is the upper bound it would be chasing.
//
// Build: cl /O2 /EHsc /std:c++17 native_copy.cpp
// Usage: native_copy.exe <src> <dst> <threads>

#include <windows.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

struct Job {
    std::wstring src;
    std::wstring dst;
};

static std::wstring extended(const std::wstring& path) {
    if (path.size() >= 4 && path.compare(0, 4, L"\\\\?\\") == 0) return path;
    return L"\\\\?\\" + path;
}

// Recursive enumeration: files into `jobs`, directories into `dirs` (parents first).
static void enumerate(const std::wstring& src, const std::wstring& dst,
                      std::vector<Job>& jobs, std::vector<std::wstring>& dirs) {
    dirs.push_back(dst);

    WIN32_FIND_DATAW data;
    const std::wstring pattern = extended(src) + L"\\*";
    HANDLE handle = FindFirstFileExW(pattern.c_str(), FindExInfoBasic, &data,
                                     FindExSearchNameMatch, nullptr,
                                     FIND_FIRST_EX_LARGE_FETCH);
    if (handle == INVALID_HANDLE_VALUE) return;

    do {
        const std::wstring name = data.cFileName;
        if (name == L"." || name == L"..") continue;

        const std::wstring child_src = src + L"\\" + name;
        const std::wstring child_dst = dst + L"\\" + name;

        if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) continue;
            enumerate(child_src, child_dst, jobs, dirs);
        } else {
            jobs.push_back({child_src, child_dst});
        }
    } while (FindNextFileW(handle, &data));

    FindClose(handle);
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) {
        std::fwprintf(stderr, L"usage: native_copy <src> <dst> [threads]\n");
        return 2;
    }
    const std::wstring src = argv[1];
    const std::wstring dst = argv[2];
    const unsigned threads = (argc > 3) ? _wtoi(argv[3]) : 8;

    const auto start = std::chrono::steady_clock::now();

    std::vector<Job> jobs;
    std::vector<std::wstring> dirs;
    jobs.reserve(64 * 1024);
    enumerate(src, dst, jobs, dirs);

    const auto scanned = std::chrono::steady_clock::now();

    // Parents come before children, so a single pass suffices.
    for (const auto& dir : dirs) CreateDirectoryW(extended(dir).c_str(), nullptr);

    std::atomic<size_t> next{0};
    std::atomic<size_t> failed{0};
    std::vector<std::thread> pool;
    pool.reserve(threads);

    for (unsigned t = 0; t < threads; ++t) {
        pool.emplace_back([&] {
            for (;;) {
                const size_t i = next.fetch_add(1, std::memory_order_relaxed);
                if (i >= jobs.size()) return;
                const std::wstring s = extended(jobs[i].src);
                const std::wstring d = extended(jobs[i].dst);
                if (!CopyFileW(s.c_str(), d.c_str(), FALSE)) {
                    failed.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }
    for (auto& thread : pool) thread.join();

    const auto done = std::chrono::steady_clock::now();
    const double scan_s = std::chrono::duration<double>(scanned - start).count();
    const double total_s = std::chrono::duration<double>(done - start).count();

    std::wprintf(L"files=%zu dirs=%zu failed=%zu threads=%u scan=%.2fs total=%.2fs %.0f files/s\n",
                 jobs.size(), dirs.size(), failed.load(), threads, scan_s, total_s,
                 jobs.size() / total_s);
    return failed.load() ? 1 : 0;
}
