"""Fixed, parameterized read-only SQLite tools with dynamic corpus bounds. No shell or arbitrary SQL."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .ingest import DB

ALLOWED_LEVELS = (None, "error", "notice", "warn", "info", "debug")


class LogTools:
    def __init__(self, db: Path = DB):
        self.db = Path(db)
        self.max_line = self._fetch_max_line()
        self.dataset = self._fetch_meta("dataset", "apache")

    def _select(self, sql: str, args: tuple = ()) -> list[dict]:
        with sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            return [dict(x) for x in c.execute(sql, args)]

    def _fetch_max_line(self) -> int:
        if not self.db.exists():
            return 2000
        rows = self._select("SELECT COALESCE(MAX(line), 2000) AS m FROM logs")
        return max(1, int(rows[0]["m"])) if rows else 2000

    def _fetch_meta(self, key: str, default: str) -> str:
        if not self.db.exists():
            return default
        try:
            rows = self._select("SELECT value FROM meta WHERE key = ?", (key,))
            return str(rows[0]["value"]) if rows else default
        except sqlite3.OperationalError:
            return default

    def search(
        self,
        contains: str = "",
        level: str | None = None,
        limit: int = 20,
        start: int = 1,
        end: int | None = None,
    ) -> list[dict]:
        end_line = self.max_line if end is None else end
        if not 1 <= limit <= 100 or not 1 <= start <= end_line <= self.max_line:
            raise ValueError(f"Invalid bounds (valid lines: 1..{self.max_line})")
        if level not in ALLOWED_LEVELS:
            raise ValueError("Invalid level")
        q = contains.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        return self._select(
            "SELECT * FROM logs WHERE message LIKE ? ESCAPE '!' "
            "AND (? IS NULL OR level=?) AND line BETWEEN ? AND ? ORDER BY line LIMIT ?",
            ("%" + q + "%", level, level, start, end_line, limit),
        )

    def context(self, line: int, radius: int = 2) -> list[dict]:
        if not 1 <= line <= self.max_line or not 0 <= radius <= 5:
            raise ValueError(f"Invalid context bounds (valid lines: 1..{self.max_line})")
        return self._select(
            "SELECT * FROM logs WHERE line BETWEEN ? AND ? ORDER BY line",
            (max(1, line - radius), min(self.max_line, line + radius)),
        )

    def stats(self) -> list[dict]:
        return self._select("SELECT level, COUNT(*) AS count FROM logs GROUP BY level ORDER BY level")

    def all(self) -> list[dict]:
        return self._select("SELECT * FROM logs ORDER BY line")

    def filtered_stats(
        self,
        contains: str = "",
        level: str | None = None,
        start: int = 1,
        end: int | None = None,
    ) -> list[dict]:
        end_line = self.max_line if end is None else end
        if not 1 <= start <= end_line <= self.max_line:
            raise ValueError(f"Invalid bounds (valid lines: 1..{self.max_line})")
        if level not in ALLOWED_LEVELS:
            raise ValueError("Invalid level")
        q = contains.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        return self._select(
            "SELECT level, COUNT(*) AS count FROM logs WHERE message LIKE ? ESCAPE '!' "
            "AND (? IS NULL OR level=?) AND line BETWEEN ? AND ? GROUP BY level ORDER BY level",
            ("%" + q + "%", level, level, start, end_line),
        )
