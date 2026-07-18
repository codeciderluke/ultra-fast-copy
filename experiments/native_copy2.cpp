// native_copy2 -- an *improved* Robocopy-style copier for the ufCopy benchmarks,
// and a demonstration of the ONE file-by-file trick that actually beats Robocopy
// on NTFS: scatter concurrent writes across directories so threads do not fight
// over one directory's index lock.
//
// The story (measured -- see docs/benchmark.md):
//   * native_copy.cpp (naive: CopyFileW from a thread pool, in enumeration order)
//     LOSES to Robocopy. So does this program with --noscatter.
//   * The reason is NOT the copy loop. An NTFS directory is one B-tree behind one
//     lock; 16 threads all creating files into the SAME destination folder (which
//     is what enumeration order gives you, and what Robocopy's per-directory /MT
//     does) serialize on that lock.
//   * Fix: round-robin the work across directories, so at any instant the 16
//     threads are inserting into 16 DIFFERENT destination folders. Contention
//     disappears and this beats Robocopy by ~25%.
//
// Two things that seemed like improvements and were NOT (kept as flags so you can
// re-measure): a hand-rolled read/write loop (--lean is the default off; CopyFileW
// is the optimized kernel path and beats hand code by ~1.8x), and largest-file-first
// scheduling (irrelevant when files are uniformly small -- it only helped earlier by
// accidentally scattering directories, which we now do on purpose).
//
// Feature parity, opt-in: the raw scatter copy dropped everything Robocopy's engine
// gives you. Those are back as OPTIONAL flags -- and off by default, so the
// benchmark numbers still measure the bare copy:
//   --update        change comparison + file-level resume: skip a file whose dest
//                   already has the same size and modified-time (re-run resumes).
//   --mirror        --update, plus delete dest files/dirs that are not in the source.
//   --retry N       retry a failed copy up to N times.
//   --wait MS       wait between retries (default 250 ms).
//
// Build: cl /O2 /EHsc /std:c++17 native_copy2.cpp
// Usage: native_copy2.exe <src> <dst> [threads]
//                         [--noscatter] [--lean] [--buf-kb N]
//                         [--update] [--mirror] [--retry N] [--wait MS]

#include <windows.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

struct Job {
    std::wstring src;
    std::wstring dst;
    size_t       dir;  // index into `dirs`: the destination directory this file lands in
};

static std::wstring extended(const std::wstring& path) {
    if (path.size() >= 4 && path.compare(0, 4, L"\\\\?\\") == 0) return path;
    return L"\\\\?\\" + path;
}

static std::wstring lower(std::wstring s) {
    for (auto& c : s) c = towlower(c);
    return s;  // NTFS is case-insensitive; lowercase for set keys
}

struct Stat {
    bool     exists = false;
    uint64_t size = 0;
    FILETIME mtime{};
};

static Stat stat_file(const std::wstring& p) {
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExW(extended(p).c_str(), GetFileExInfoStandard, &a)) return {};
    return {true, (static_cast<uint64_t>(a.nFileSizeHigh) << 32) | a.nFileSizeLow, a.ftLastWriteTime};
}

// Robocopy's "same file" test: dest exists with identical size and modified-time.
static bool dest_current(const std::wstring& src, const std::wstring& dst) {
    const Stat s = stat_file(src), d = stat_file(dst);
    return s.exists && d.exists && s.size == d.size && CompareFileTime(&s.mtime, &d.mtime) == 0;
}

static void enumerate(const std::wstring& src, const std::wstring& dst,
                      std::vector<Job>& jobs, std::vector<std::wstring>& dirs) {
    const size_t my_dir = dirs.size();
    dirs.push_back(dst);

    WIN32_FIND_DATAW data;
    const std::wstring pattern = extended(src) + L"\\*";
    HANDLE handle = FindFirstFileExW(pattern.c_str(), FindExInfoBasic, &data,
                                     FindExSearchNameMatch, nullptr, FIND_FIRST_EX_LARGE_FETCH);
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
            jobs.push_back({child_src, child_dst, my_dir});
        }
    } while (FindNextFileW(handle, &data));

    FindClose(handle);
}

static void scatter_by_directory(std::vector<Job>& jobs, size_t dir_count) {
    std::vector<std::vector<size_t>> by_dir(dir_count);
    for (size_t i = 0; i < jobs.size(); ++i) by_dir[jobs[i].dir].push_back(i);

    std::vector<Job> ordered;
    ordered.reserve(jobs.size());
    std::vector<size_t> cursor(dir_count, 0);
    size_t remaining = jobs.size();
    while (remaining) {
        for (size_t d = 0; d < dir_count; ++d) {
            if (cursor[d] < by_dir[d].size()) {
                ordered.push_back(std::move(jobs[by_dir[d][cursor[d]++]]));
                --remaining;
            }
        }
    }
    jobs.swap(ordered);
}

// Hand-rolled copy (default OFF). Kept only to demonstrate it loses to CopyFileW.
// Preserves the source timestamps so --update works across runs (CopyFileW does
// this itself; a raw Read/Write loop does not).
static bool copy_lean(const std::wstring& src, const std::wstring& dst, char* buf, DWORD bufsize) {
    HANDLE s = CreateFileW(extended(src).c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (s == INVALID_HANDLE_VALUE) return false;
    HANDLE d = CreateFileW(extended(dst).c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (d == INVALID_HANDLE_VALUE) { CloseHandle(s); return false; }
    bool ok = true;
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(s, buf, bufsize, &got, nullptr)) { ok = false; break; }
        if (got == 0) break;
        DWORD put = 0;
        if (!WriteFile(d, buf, got, &put, nullptr) || put != got) { ok = false; break; }
    }
    if (ok) {
        FILETIME ct, at, wt;
        if (GetFileTime(s, &ct, &at, &wt)) SetFileTime(d, &ct, &at, &wt);
    }
    CloseHandle(d);
    CloseHandle(s);
    return ok;
}

static bool do_copy(const Job& j, bool lean, char* buf, DWORD bufsize) {
    return lean ? copy_lean(j.src, j.dst, buf, bufsize)
                : CopyFileW(extended(j.src).c_str(), extended(j.dst).c_str(), FALSE) != 0;
}

static bool copy_with_retry(const Job& j, bool lean, char* buf, DWORD bufsize,
                            int retries, DWORD wait_ms) {
    for (int attempt = 0; ; ++attempt) {
        if (do_copy(j, lean, buf, bufsize)) return true;
        if (attempt >= retries) return false;
        Sleep(wait_ms);
    }
}

// --mirror: delete anything under the destination that the source did not produce.
static void remove_tree(const std::wstring& dir, std::atomic<size_t>& deleted) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW((extended(dir) + L"\\*").c_str(), FindExInfoBasic, &fd,
                                FindExSearchNameMatch, nullptr, FIND_FIRST_EX_LARGE_FETCH);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            const std::wstring name = fd.cFileName;
            if (name == L"." || name == L"..") continue;
            const std::wstring full = dir + L"\\" + name;
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) &&
                !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
                remove_tree(full, deleted);
            } else {
                SetFileAttributesW(extended(full).c_str(), FILE_ATTRIBUTE_NORMAL);
                if (DeleteFileW(extended(full).c_str())) deleted.fetch_add(1, std::memory_order_relaxed);
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(extended(dir).c_str());
}

static void purge_extraneous(const std::wstring& dir,
                             const std::unordered_set<std::wstring>& keep_dirs,
                             const std::unordered_set<std::wstring>& keep_files,
                             std::atomic<size_t>& deleted) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW((extended(dir) + L"\\*").c_str(), FindExInfoBasic, &fd,
                                FindExSearchNameMatch, nullptr, FIND_FIRST_EX_LARGE_FETCH);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        const std::wstring name = fd.cFileName;
        if (name == L"." || name == L"..") continue;
        const std::wstring full = dir + L"\\" + name;
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) &&
            !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
            if (keep_dirs.count(lower(full))) purge_extraneous(full, keep_dirs, keep_files, deleted);
            else remove_tree(full, deleted);  // whole subtree not in source
        } else if (!keep_files.count(lower(full))) {
            SetFileAttributesW(extended(full).c_str(), FILE_ATTRIBUTE_NORMAL);
            if (DeleteFileW(extended(full).c_str())) deleted.fetch_add(1, std::memory_order_relaxed);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) {
        std::fwprintf(stderr, L"usage: native_copy2 <src> <dst> [threads] [--noscatter] [--lean] "
                              L"[--buf-kb N] [--update] [--mirror] [--retry N] [--wait MS]\n");
        return 2;
    }
    const std::wstring src = argv[1];
    const std::wstring dst = argv[2];

    unsigned threads = 16;
    bool no_scatter = false, use_lean = false, update = false, mirror = false;
    DWORD bufsize = 1u << 20, wait_ms = 250;
    int retries = 0;
    for (int i = 3; i < argc; ++i) {
        const std::wstring a = argv[i];
        if (a == L"--noscatter") no_scatter = true;
        else if (a == L"--lean") use_lean = true;
        else if (a == L"--update") update = true;
        else if (a == L"--mirror") { mirror = true; update = true; }
        else if (a == L"--retry" && i + 1 < argc) retries = _wtoi(argv[++i]);
        else if (a == L"--wait" && i + 1 < argc) wait_ms = static_cast<DWORD>(_wtoi(argv[++i]));
        else if (a == L"--buf-kb" && i + 1 < argc) bufsize = static_cast<DWORD>(_wtoi(argv[++i])) * 1024u;
        else if (!a.empty() && iswdigit(a[0])) threads = static_cast<unsigned>(_wtoi(a.c_str()));
    }
    if (threads < 1) threads = 1;

    const auto start = std::chrono::steady_clock::now();

    std::vector<Job> jobs;
    std::vector<std::wstring> dirs;
    jobs.reserve(64 * 1024);
    enumerate(src, dst, jobs, dirs);

    if (!no_scatter) scatter_by_directory(jobs, dirs.size());

    const auto scanned = std::chrono::steady_clock::now();

    for (const auto& dir : dirs) CreateDirectoryW(extended(dir).c_str(), nullptr);

    std::atomic<size_t> next{0}, failed{0}, skipped{0}, deleted{0};
    std::vector<std::thread> pool;
    pool.reserve(threads);

    for (unsigned t = 0; t < threads; ++t) {
        pool.emplace_back([&] {
            std::vector<char> buf(use_lean ? bufsize : 1);  // reused per-thread buffer
            for (;;) {
                const size_t i = next.fetch_add(1, std::memory_order_relaxed);
                if (i >= jobs.size()) return;
                if (update && dest_current(jobs[i].src, jobs[i].dst)) {
                    skipped.fetch_add(1, std::memory_order_relaxed);
                    continue;
                }
                if (!copy_with_retry(jobs[i], use_lean, buf.data(), bufsize, retries, wait_ms))
                    failed.fetch_add(1, std::memory_order_relaxed);
            }
        });
    }
    for (auto& thread : pool) thread.join();

    // --mirror: after copying, remove destination entries the source did not create.
    if (mirror) {
        std::unordered_set<std::wstring> keep_dirs, keep_files;
        keep_dirs.reserve(dirs.size());
        keep_files.reserve(jobs.size());
        for (const auto& d : dirs) keep_dirs.insert(lower(d));
        for (const auto& j : jobs) keep_files.insert(lower(j.dst));
        purge_extraneous(dst, keep_dirs, keep_files, deleted);
    }

    const auto done = std::chrono::steady_clock::now();
    const double scan_s = std::chrono::duration<double>(scanned - start).count();
    const double total_s = std::chrono::duration<double>(done - start).count();

    std::wprintf(L"files=%zu dirs=%zu copied=%zu skipped=%zu deleted=%zu failed=%zu "
                 L"threads=%u copy=%s order=%s scan=%.2fs total=%.2fs %.0f files/s\n",
                 jobs.size(), dirs.size(), jobs.size() - skipped.load() - failed.load(),
                 skipped.load(), deleted.load(), failed.load(), threads,
                 use_lean ? L"lean" : L"CopyFileW", no_scatter ? L"enum" : L"scatter",
                 scan_s, total_s, jobs.size() / total_s);
    return failed.load() ? 1 : 0;
}
