"""
collectors/system.py
System-level metric collectors using psutil.
"""

from __future__ import annotations
import os
import socket
import platform
import time
from datetime import datetime
import psutil

from collectors.base import BaseCollector


class CpuCollector(BaseCollector):
    def collect(self) -> dict:
        percent    = psutil.cpu_percent(interval=1)
        count      = psutil.cpu_count()
        load_avg   = os.getloadavg()
        load_ratio = round(load_avg[0] / max(count, 1), 2)
        return {
            "percent"   : percent,
            "core_count": count,
            "load_1m"   : round(load_avg[0], 2),
            "load_5m"   : round(load_avg[1], 2),
            "load_15m"  : round(load_avg[2], 2),
            "load_ratio": load_ratio,
        }


class MemoryCollector(BaseCollector):
    def collect(self) -> dict:
        mem  = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total_gb"     : round(mem.total / 1e9, 1),
            "used_gb"      : round(mem.used   / 1e9, 1),
            "available_gb" : round(mem.available / 1e9, 1),
            "percent"      : mem.percent,
            "swap_total_gb": round(swap.total / 1e9, 1),
            "swap_used_gb" : round(swap.used  / 1e9, 1),
            "swap_percent" : swap.percent,
        }


class DiskCollector(BaseCollector):
    SKIP_FSTYPES = {"", "tmpfs", "devtmpfs", "squashfs"}

    def collect(self) -> list:
        disks = []
        for part in psutil.disk_partitions():
            if part.fstype in self.SKIP_FSTYPES:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "mount"   : part.mountpoint,
                    "total_gb": round(usage.total / 1e9, 1),
                    "used_gb" : round(usage.used  / 1e9, 1),
                    "free_gb" : round(usage.free  / 1e9, 1),
                    "percent" : usage.percent,
                })
            except PermissionError:
                continue
        return disks


class NetworkCollector(BaseCollector):
    def collect(self) -> dict:
        net   = psutil.net_io_counters()
        conns = psutil.net_connections()
        state_count: dict[str, int] = {}
        for c in conns:
            s = c.status or "NONE"
            state_count[s] = state_count.get(s, 0) + 1

        reachable = True
        try:
            socket.setdefaulttimeout(3)
            socket.socket().connect(("8.8.8.8", 53))
        except Exception:
            reachable = False

        return {
            "bytes_sent_mb": round(net.bytes_sent / 1e6, 1),
            "bytes_recv_mb": round(net.bytes_recv / 1e6, 1),
            "err_in"       : net.errin,
            "err_out"      : net.errout,
            "conn_by_state": state_count,
            "internet_ok"  : reachable,
        }


class OsCollector(BaseCollector):
    # psutil returns 0.0 on the first cpu_percent() call for a process, so
    # usage is sampled over this window (CpuCollector already blocks for 1s).
    CPU_SAMPLE_SEC = 0.5
    TOP_N          = 5

    def collect(self) -> dict:
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime_h  = round((datetime.now() - boot_time).total_seconds() / 3600, 1)
        my_pid    = os.getpid()

        zombies: list[dict] = []
        for p in psutil.process_iter(["pid", "status", "name", "ppid"]):
            info = p.info
            if (info["status"] == psutil.STATUS_ZOMBIE
                    and info.get("name") != "run-parts"
                    and info.get("ppid") != my_pid):
                zombies.append({"pid": info["pid"], "ppid": info.get("ppid"), "name": info.get("name")})

        procs = list(psutil.process_iter(["pid", "name"]))
        for p in procs:
            try:
                p.cpu_percent(None)  # prime
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(self.CPU_SAMPLE_SEC)

        samples: list[dict] = []
        for p in procs:
            try:
                with p.oneshot():
                    samples.append({
                        "pid"           : p.pid,
                        "name"          : p.info.get("name"),
                        "cpu_percent"   : round(p.cpu_percent(None), 1),
                        "memory_percent": round(p.memory_percent(), 1),
                        "rss_mb"        : round(p.memory_info().rss / 1e6, 1),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        top_cpu = sorted(samples, key=lambda x: x["cpu_percent"], reverse=True)[:self.TOP_N]
        top_mem = sorted(samples, key=lambda x: x["rss_mb"], reverse=True)[:self.TOP_N]

        return {
            "hostname"     : socket.gethostname(),
            "os"           : f"{platform.system()} {platform.release()}",
            "uptime_hours" : uptime_h,
            "zombie_count" : len(zombies),
            "zombies"      : zombies[:self.TOP_N],
            "top_cpu_procs": top_cpu,
            "top_mem_procs": top_mem,
        }
