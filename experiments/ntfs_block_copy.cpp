// Block-level NTFS volume copier with a VSS snapshot, to find the real ceiling
// below the filesystem -- consistently.
//
// The thesis (see docs/benchmark.md): on NTFS the per-file metadata cost -- MFT
// record, directory-index insert, $LogFile/USN journal, and the antivirus
// minifilter -- is unavoidable *if files must materialize individually*. This
// program refuses that framing. It never opens a single file. It asks NTFS
// which clusters are allocated (FSCTL_GET_VOLUME_BITMAP) and streams only those
// clusters sequentially from the raw volume. Per-file cost drops to zero, so
// this is the upper bound a block-image tool (Macrium, dd) chases -- the one
// approach that beats Robocopy on NTFS by an order of magnitude.
//
// Consistency: by default it first takes a VSS snapshot (IVssBackupComponents,
// VSS_BT_COPY) and reads from the shadow-copy device, so the image is a coherent
// point in time even while the volume is in use. --live skips VSS and reads the
// mounted volume directly (fast, but a torn image if anything writes mid-copy).
//
// Snapshot is crash-consistent, not application-consistent: writer metadata is
// not gathered, so in-flight app buffers are not flushed. NTFS recovers via its
// journal exactly as it would after a power loss. That is the deliberate trade
// for a block imager; gathering writers is a documented extension.
//
// It reads the raw volume, so it is *volume-granular*: it images an entire
// drive, not an arbitrary subtree. That is the trade the speed costs.
//
// Build: cl /O2 /EHsc /std:c++17 ntfs_block_copy.cpp /link vssapi.lib ole32.lib
// Usage: ntfs_block_copy.exe <SourceDrive> <OutImage|--read-only> [--chunk-mb N] [--live]
//   ntfs_block_copy.exe D: E:\d.img        # snapshot D:, image its used clusters
//   ntfs_block_copy.exe D: --read-only     # snapshot D:, read used clusters, discard
//   ntfs_block_copy.exe D: --read-only --live   # skip VSS, read the live volume
//
// Requires an elevated (Administrator) prompt: both \\.\<drive> and VSS need it.
//
// No verification, no retry, no progress. Minimal by design, like
// native_copy.cpp -- it measures a ceiling, it is not a product.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winioctl.h>
#include <vss.h>
#include <vswriter.h>
#include <vsbackup.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <string>

// ---- small helpers -------------------------------------------------------

static bool check(HRESULT hr, const wchar_t* what) {
    if (FAILED(hr)) { std::fwprintf(stderr, L"%s failed (hr=0x%08lX)\n", what, static_cast<unsigned long>(hr)); return false; }
    return true;
}

// VSS operations are asynchronous: wait, then surface the operation's own status.
static HRESULT wait_async(IVssAsync* a) {
    if (!a) return E_UNEXPECTED;
    HRESULT hr = a->Wait();
    if (SUCCEEDED(hr)) { HRESULT status = E_FAIL; a->QueryStatus(&status, nullptr); hr = status; }
    a->Release();
    return hr;
}

// One aligned scratch buffer serves both the volume read and the image write.
// FILE_FLAG_NO_BUFFERING demands sector-aligned offsets, lengths, and buffers;
// cluster-aligned runs satisfy that, and VirtualAlloc is page-aligned.
struct Aligned {
    void* p = nullptr;
    explicit Aligned(size_t bytes) { p = VirtualAlloc(nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE); }
    ~Aligned() { if (p) VirtualFree(p, 0, MEM_RELEASE); }
};

// ---- VSS snapshot (RAII: releasing the components deletes the snapshot) ----

struct Vss {
    IVssBackupComponents* bc = nullptr;
    bool coinit = false;
    std::wstring device;  // \\?\GLOBALROOT\Device\HarddiskVolumeShadowCopyN
    ~Vss() {
        if (bc) bc->Release();       // non-persistent VSS_CTX_BACKUP: snapshot auto-deletes here
        if (coinit) CoUninitialize();
    }
};

static bool make_snapshot(const std::wstring& drive, Vss& v) {
    if (!check(CoInitializeEx(nullptr, COINIT_MULTITHREADED), L"CoInitializeEx")) return false;
    v.coinit = true;
    // Without packet-privacy security, DoSnapshotSet fails with access-denied.
    CoInitializeSecurity(nullptr, -1, nullptr, nullptr, RPC_C_AUTHN_LEVEL_PKT_PRIVACY,
                         RPC_C_IMP_LEVEL_IDENTIFY, nullptr, EOAC_NONE, nullptr);

    if (!check(CreateVssBackupComponents(&v.bc), L"CreateVssBackupComponents")) return false;
    if (!check(v.bc->InitializeForBackup(), L"InitializeForBackup")) return false;
    if (!check(v.bc->SetContext(VSS_CTX_BACKUP), L"SetContext")) return false;
    // Copy backup, no component selection, no bootable-state, no partial files.
    if (!check(v.bc->SetBackupState(false, false, VSS_BT_COPY, false), L"SetBackupState")) return false;

    VSS_ID setId{};
    if (!check(v.bc->StartSnapshotSet(&setId), L"StartSnapshotSet")) return false;

    // AddToSnapshotSet wants a mutable "X:\" with a trailing backslash.
    wchar_t vol[8] = {};
    swprintf(vol, 8, L"%c%c\\", drive[0], drive.size() > 1 ? drive[1] : L':');

    VSS_ID snapId{};
    if (!check(v.bc->AddToSnapshotSet(vol, GUID_NULL, &snapId), L"AddToSnapshotSet")) return false;

    IVssAsync* a = nullptr;
    if (!check(v.bc->PrepareForBackup(&a), L"PrepareForBackup")) return false;
    if (!check(wait_async(a), L"PrepareForBackup(wait)")) return false;
    if (!check(v.bc->DoSnapshotSet(&a), L"DoSnapshotSet")) return false;
    if (!check(wait_async(a), L"DoSnapshotSet(wait)")) return false;

    VSS_SNAPSHOT_PROP prop{};
    if (!check(v.bc->GetSnapshotProperties(snapId, &prop), L"GetSnapshotProperties")) return false;
    v.device = prop.m_pwszSnapshotDeviceObject;
    VssFreeSnapshotProperties(&prop);
    return true;
}

// ---- block copy engine ---------------------------------------------------

static HANDLE open_image(const std::wstring& path) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                           FILE_FLAG_NO_BUFFERING, nullptr);
    if (h == INVALID_HANDLE_VALUE) return h;
    // Sparse: free-space gaps between allocated runs never hit the disk -- the
    // image's logical size equals the volume, its physical size only the used clusters.
    DWORD ret = 0;
    DeviceIoControl(h, FSCTL_SET_SPARSE, nullptr, 0, nullptr, 0, &ret, nullptr);
    return h;
}

// Copy [offset, offset+len) straight through the raw device, in <= chunk reads.
static bool copy_run(HANDLE dev, HANDLE img, uint64_t offset, uint64_t len, void* buf, DWORD chunk) {
    while (len > 0) {
        const DWORD n = (len < chunk) ? static_cast<DWORD>(len) : chunk;

        LARGE_INTEGER at;
        at.QuadPart = static_cast<LONGLONG>(offset);
        if (!SetFilePointerEx(dev, at, nullptr, FILE_BEGIN)) return false;
        DWORD got = 0;
        if (!ReadFile(dev, buf, n, &got, nullptr) || got != n) return false;

        if (img != INVALID_HANDLE_VALUE) {
            if (!SetFilePointerEx(img, at, nullptr, FILE_BEGIN)) return false;
            DWORD put = 0;
            if (!WriteFile(img, buf, n, &put, nullptr) || put != n) return false;
        }
        offset += n;
        len -= n;
    }
    return true;
}

// Walk the allocation bitmap of an already-open volume/shadow device, copying
// every allocated run. Fills `allocated` with the cluster count copied.
static bool run_bitmap_copy(HANDLE dev, uint64_t cluster, HANDLE img, DWORD chunk, uint64_t& allocated) {
    Aligned scratch(chunk);
    if (!scratch.p) { std::fwprintf(stderr, L"alloc failed\n"); return false; }

    // Big output buffer -> many clusters per FSCTL call. ERROR_MORE_DATA means loop.
    constexpr DWORD BITMAP_BYTES = 1u << 20;  // ~8.3M clusters/call -> ~32 GB at 4K
    static BYTE bitmap[BITMAP_BYTES];
    const uint64_t cap_bits = static_cast<uint64_t>(BITMAP_BYTES - sizeof(VOLUME_BITMAP_BUFFER)) * 8;

    STARTING_LCN_INPUT_BUFFER in{};
    in.StartingLcn.QuadPart = 0;

    uint64_t run_start = 0, run_len = 0;
    DWORD ret = 0;

    for (;;) {
        const BOOL done = DeviceIoControl(dev, FSCTL_GET_VOLUME_BITMAP, &in, sizeof in,
                                          bitmap, BITMAP_BYTES, &ret, nullptr);
        const DWORD err = done ? ERROR_SUCCESS : GetLastError();
        if (!done && err != ERROR_MORE_DATA) {
            std::fwprintf(stderr, L"get volume bitmap failed (err %lu)\n", err);
            return false;
        }

        const auto* vb = reinterpret_cast<const VOLUME_BITMAP_BUFFER*>(bitmap);
        const uint64_t base = static_cast<uint64_t>(vb->StartingLcn.QuadPart);
        uint64_t bits = static_cast<uint64_t>(vb->BitmapSize.QuadPart);
        if (bits > cap_bits) bits = cap_bits;  // never read past what the buffer holds

        for (uint64_t i = 0; i < bits; ++i) {
            const BYTE byte = vb->Buffer[i >> 3];
            // Fast-skip a fully-free byte (8 clusters) so big free extents stay cheap.
            if ((i & 7) == 0 && byte == 0x00) {
                if (run_len) {
                    if (!copy_run(dev, img, run_start * cluster, run_len * cluster, scratch.p, chunk)) return false;
                    allocated += run_len;
                    run_len = 0;
                }
                i += 7;
                continue;
            }
            if ((byte >> (i & 7)) & 1) {
                if (run_len == 0) run_start = base + i;
                ++run_len;
            } else if (run_len) {
                if (!copy_run(dev, img, run_start * cluster, run_len * cluster, scratch.p, chunk)) return false;
                allocated += run_len;
                run_len = 0;
            }
        }

        if (done) break;  // no ERROR_MORE_DATA: last chunk consumed
        in.StartingLcn.QuadPart = static_cast<LONGLONG>(base + bits);
    }

    // Flush the run still open at the tail of the last bitmap chunk.
    if (run_len) {
        if (!copy_run(dev, img, run_start * cluster, run_len * cluster, scratch.p, chunk)) return false;
        allocated += run_len;
    }
    return true;
}

// ---- main ----------------------------------------------------------------

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) {
        std::fwprintf(stderr, L"usage: ntfs_block_copy <SourceDrive> <OutImage|--read-only> [--chunk-mb N] [--live]\n");
        return 2;
    }
    std::wstring drive = argv[1];
    if (!drive.empty() && drive.back() == L'\\') drive.pop_back();  // "D:\" -> "D:"

    const std::wstring out = argv[2];
    const bool read_only = (out == L"--read-only");

    DWORD chunk = 4u * 1024 * 1024;
    bool live = false;
    for (int i = 3; i < argc; ++i) {
        const std::wstring a = argv[i];
        if (a == L"--live") live = true;
        else if (a == L"--chunk-mb" && i + 1 < argc) chunk = static_cast<DWORD>(_wtoi(argv[++i])) * 1024u * 1024u;
    }

    // Pick the device to read: a fresh VSS snapshot, or the live volume.
    Vss vss;
    std::wstring dev_path;
    if (live) {
        dev_path = L"\\\\.\\" + drive;  // \\.\D:
    } else {
        std::fwprintf(stderr, L"creating VSS snapshot of %s ...\n", drive.c_str());
        if (!make_snapshot(drive, vss)) return 1;
        dev_path = vss.device;
        std::fwprintf(stderr, L"snapshot device: %s\n", dev_path.c_str());
    }

    HANDLE dev = CreateFileW(dev_path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             nullptr, OPEN_EXISTING, FILE_FLAG_NO_BUFFERING, nullptr);
    if (dev == INVALID_HANDLE_VALUE) {
        std::fwprintf(stderr, L"open %s failed (err %lu) -- run elevated?\n", dev_path.c_str(), GetLastError());
        return 1;
    }

    // Volume geometry. This FSCTL also fails on non-NTFS, which is the check we want.
    NTFS_VOLUME_DATA_BUFFER vd{};
    DWORD ret = 0;
    if (!DeviceIoControl(dev, FSCTL_GET_NTFS_VOLUME_DATA, nullptr, 0, &vd, sizeof vd, &ret, nullptr)) {
        std::fwprintf(stderr, L"%s is not NTFS, or volume data unavailable (err %lu)\n", drive.c_str(), GetLastError());
        CloseHandle(dev);
        return 1;
    }
    const uint64_t cluster = vd.BytesPerCluster;
    const uint64_t total_clusters = static_cast<uint64_t>(vd.TotalClusters.QuadPart);

    HANDLE img = INVALID_HANDLE_VALUE;
    if (!read_only) {
        img = open_image(out);
        if (img == INVALID_HANDLE_VALUE) {
            std::fwprintf(stderr, L"create image %s failed (err %lu)\n", out.c_str(), GetLastError());
            CloseHandle(dev);
            return 1;
        }
    }

    const auto start = std::chrono::steady_clock::now();
    uint64_t allocated = 0;
    const bool ok = run_bitmap_copy(dev, cluster, img, chunk, allocated);
    const auto finish = std::chrono::steady_clock::now();
    const double secs = std::chrono::duration<double>(finish - start).count();

    // Give the image the volume's full logical size, so the sparse file is a
    // faithful raw image (holes where the volume's free space is).
    if (ok && img != INVALID_HANDLE_VALUE) {
        LARGE_INTEGER end;
        end.QuadPart = static_cast<LONGLONG>(total_clusters * cluster);
        if (SetFilePointerEx(img, end, nullptr, FILE_BEGIN)) SetEndOfFile(img);
    }

    if (img != INVALID_HANDLE_VALUE) CloseHandle(img);
    CloseHandle(dev);
    if (!ok) return 1;

    const double used_mb = static_cast<double>(allocated * cluster) / (1024.0 * 1024.0);
    const double total_mb = static_cast<double>(total_clusters * cluster) / (1024.0 * 1024.0);
    std::wprintf(L"drive=%s source=%s cluster=%llu total=%.0fMB used=%.0fMB (%.1f%%) copied=%s "
                 L"time=%.2fs %.0f MB/s\n",
                 drive.c_str(), live ? L"live" : L"vss-snapshot",
                 static_cast<unsigned long long>(cluster), total_mb, used_mb,
                 total_mb > 0 ? 100.0 * used_mb / total_mb : 0.0,
                 read_only ? L"no(read-only)" : out.c_str(),
                 secs, secs > 0 ? used_mb / secs : 0.0);
    return 0;
}
