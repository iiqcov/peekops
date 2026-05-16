"""
collectors/system.py
System-level metric collectors using psutil.
"""

from __future__ import annotations
import os
import socket
import platform
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
    def collect(self) -> dict:
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime_h  = round((datetime.now() - boot_time).total_seconds() / 3600, 1)
        my_pid    = os.getpid()

        zombies = [
            p for p in psutil.process_iter(["status", "name", "ppid"])
            if p.info["status"] == "zombie"
            and p.info.get("name") != "run-parts"
            and p.info.get("ppid") != my_pid
        ]

        procs: list[dict] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        top_cpu = sorted(procs, key=lambda x: x.get("cpu_percent", 0), reverse=True)[:5]

        return {
            "hostname"     : socket.gethostname(),
            "os"           : f"{platform.system()} {platform.release()}",
            "uptime_hours" : uptime_h,
            "zombie_count" : len(zombies),
            "top_cpu_procs": top_cpu,
        }
