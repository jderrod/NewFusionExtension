"""Windows process metrics via ctypes (no psutil needed, works inside Fusion).

Used by the add-in (its own process) and by the harness (the Fusion process).
"""

import ctypes
import os
import subprocess
import sys
import time

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010
    PROCESS_TERMINATE = 0x0001
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                            wintypes.DWORD]


def list_processes():
    """[(pid, exe_name, thread_count, parent_pid)]"""
    if not IS_WINDOWS:
        return []
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.szExeFile, entry.cntThreads,
                        entry.th32ParentProcessID))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return out


def find_pids(exe_name):
    low = exe_name.lower()
    return [p[0] for p in list_processes() if p[1].lower() == low]


def process_info(pid):
    """Thread count, working set, handles and start time for a pid, or None."""
    if not IS_WINDOWS or not pid:
        return None
    threads = None
    for p in list_processes():
        if p[0] == pid:
            threads = p[2]
            break
    if threads is None:
        return None
    info = {"pid": pid, "threads": threads, "working_set_mb": None, "handles": None, "started_at": None}
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            if _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                info["working_set_mb"] = round(pmc.WorkingSetSize / (1024.0 * 1024.0), 1)
            hc = wintypes.DWORD()
            if _k32.GetProcessHandleCount(h, ctypes.byref(hc)):
                info["handles"] = hc.value
            times = [wintypes.FILETIME() for _ in range(4)]
            if _k32.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
                ft = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                info["started_at"] = ft / 1e7 - 11644473600.0
        finally:
            _k32.CloseHandle(h)
    return info


def own_process_info():
    return process_info(os.getpid())


def terminate(pid):
    if not IS_WINDOWS:
        return False
    h = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(_k32.TerminateProcess(h, 1))
    finally:
        _k32.CloseHandle(h)


def wait_for_exit(pid, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pid not in [p[0] for p in list_processes()]:
            return True
        time.sleep(0.5)
    return False


def find_fusion_launcher():
    """The real FusionLauncher.exe under the Autodesk webdeploy folder.

    Fusion's build folders also contain a FusionLauncher.exe, but only the
    streamer's copy has the FusionLauncher.exe.ini it needs; launching any other
    copy shows "Configuration file ... is missing or incomplete"."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Autodesk", "webdeploy", "production")
    best, best_mtime = None, -1
    try:
        for d in os.listdir(base):
            exe = os.path.join(base, d, "FusionLauncher.exe")
            if os.path.isfile(exe) and os.path.isfile(exe + ".ini"):
                m = os.path.getmtime(exe)
                if m > best_mtime:
                    best, best_mtime = exe, m
    except OSError:
        pass
    return best


def launch_detached(args, cwd=None):
    flags = 0
    if IS_WINDOWS:
        flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(args, cwd=cwd, creationflags=flags, close_fds=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
