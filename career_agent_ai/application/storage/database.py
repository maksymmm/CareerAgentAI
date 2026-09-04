from __future__ import annotations

from abc import ABC, abstractmethod
import sqlite3


class Database(ABC):

    @abstractmethod
    def connection(self) -> sqlite3.Connection:
        ...