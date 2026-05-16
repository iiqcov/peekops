"""
core/lock.py
PID-based lock file to prevent concurrent diagnostic runs.
"""

from __future__ import annotations
import os
import signal
import time
import config


class ProcessLock:
    def __init__(self, pid_file: str = config.PID_FILE, max_run_sec: int = config.MAX_RUN_SEC):
        self._pid_file   = pid_file
        self._max_run_sec = max_run_sec

    def acquire(self) -> bool:
        now = time.time()
        if os.path.exists(self._pid_file):
            try:
                with open(self._pid_file) as f:
                    parts    = f.read().strip().split(":")
                    old_pid  = int(parts[0])
                    old_time = float(parts[1]) if len(parts) > 1 else now
                elapsed = now - old_time
                try:
                    os.kill(old_pid, 0)
                    alive = True
                except OSError:
                    alive = False

                if alive and elapsed < self._max_run_sec:
                    print(f"  [lock] PID {old_pid} still running ({elapsed:.0f}s) → skip")
                    return False

                if alive and elapsed >= self._max_run_sec:
                    print(f"  [lock] PID {old_pid} exceeded limit → force kill")
                    try:
                        os.kill(old_pid, signal.SIGTERM)
                        time.sleep(2)
                        os.kill(old_pid, signal.SIGKILL)
                    except OSError:
                        pass
            except Exception as e:
                print(f"  [lock] read error: {e}")

        with open(self._pid_file, "w") as f:
            f.write(f"{os.getpid()}:{now}")
        return True

    def release(self) -> None:
        try:
            if os.path.exists(self._pid_file):
                os.remove(self._pid_file)
        except Exception:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
