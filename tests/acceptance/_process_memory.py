"""Process memory sampling for the Windows/Linux residency acceptance gates."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


if sys.platform == "win32":
    class _ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
            ("private_usage", ctypes.c_size_t),
        ]

    _GET_CURRENT_PROCESS = ctypes.windll.kernel32.GetCurrentProcess
    _GET_CURRENT_PROCESS.restype = ctypes.c_void_p
    _GET_PROCESS_MEMORY_INFO = ctypes.windll.psapi.GetProcessMemoryInfo
    _GET_PROCESS_MEMORY_INFO.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCountersEx),
        ctypes.c_ulong,
    ]
    _GET_PROCESS_MEMORY_INFO.restype = ctypes.c_int
    class _HeapOptimizeResourcesInformation(ctypes.Structure):
        _fields_ = [("version", ctypes.c_ulong), ("flags", ctypes.c_ulong)]

    _HEAP_OPTIMIZE_RESOURCES = 3
    _HEAP_OPTIMIZE_INFO = _HeapOptimizeResourcesInformation(1, 0)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _HEAP_SET_INFORMATION = _KERNEL32.HeapSetInformation
    _HEAP_SET_INFORMATION.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(_HeapOptimizeResourcesInformation),
        ctypes.c_size_t,
    ]
    _HEAP_SET_INFORMATION.restype = ctypes.c_int
elif sys.platform == "linux":
    _LIBC = ctypes.CDLL("libc.so.6")
    _MALLOC_TRIM = _LIBC.malloc_trim
    _MALLOC_TRIM.argtypes = [ctypes.c_size_t]
    _MALLOC_TRIM.restype = ctypes.c_int


def process_memory_bytes() -> tuple[int, int]:
    """Return RSS and private bytes after reclaiming unused allocator pages.

    Linux reports private resident pages; Windows reports private committed
    bytes. Both measurements retain live allocations. Allocator reclamation
    runs only in acceptance instrumentation, outside the engine's frame path.
    """
    if sys.platform == "win32":
        # Match Linux's malloc_trim below. With a NULL heap handle,
        # HeapOptimizeResources reclaims unused LFH caches in all process
        # heaps. CRT compaction alone does not reclaim these caches.
        # This does not evict working-set pages or free live allocations.
        if not _HEAP_SET_INFORMATION(
            None, _HEAP_OPTIMIZE_RESOURCES,
            ctypes.byref(_HEAP_OPTIMIZE_INFO), ctypes.sizeof(_HEAP_OPTIMIZE_INFO),
        ):
            raise ctypes.WinError(
                ctypes.get_last_error(), "HeapSetInformation(HeapOptimizeResources) failed"
            )
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        if not _GET_PROCESS_MEMORY_INFO(
            _GET_CURRENT_PROCESS(), ctypes.byref(counters), counters.cb
        ):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.working_set_size), int(counters.private_usage)

    if sys.platform != "linux":
        raise RuntimeError("Residency acceptance supports Windows and Linux")

    # Freed glibc arenas can otherwise add a late multi-megabyte RSS step.
    # Reclaim free pages before checking ownership leaks; live blocks remain.
    _MALLOC_TRIM(0)
    # smaps_rollup supplies precise resident accounting in one kernel snapshot.
    fields = {}
    for line in Path("/proc/self/smaps_rollup").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition(":")
        if key in {"Rss", "Private_Clean", "Private_Dirty"}:
            fields[key] = int(value.split()[0]) * 1024
    return fields["Rss"], fields["Private_Clean"] + fields["Private_Dirty"]
