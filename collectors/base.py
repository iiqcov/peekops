"""
collectors/base.py
Abstract base class for all metric collectors.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class BaseCollector(ABC):
    """All collectors must implement collect()."""

    @abstractmethod
    def collect(self) -> dict[str, Any] | list:
        ...

    def safe_collect(self) -> dict[str, Any] | list:
        try:
            return self.collect()
        except Exception as e:
            return {"error": str(e)}
