"""SQLite 状态存储：记录已见过的 DOI、摘要、总结结果与推送状态。

状态流转:  new  ->  summarized  ->  reported
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    doi            TEXT PRIMARY KEY,
    journal        TEXT NOT NULL,
    title          TEXT NOT NULL,
    authors        TEXT,               -- JSON list
    url            TEXT,
    published      TEXT,               -- 在线发表日期 YYYY-MM-DD
    created        TEXT,               -- Crossref DOI 注册日期
    abstract       TEXT,
    abstract_src   TEXT,
    first_seen     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'new',
    summary        TEXT,
    contribution   TEXT,
    methods        TEXT,               -- JSON list
    applications   TEXT,               -- JSON list
    relevance      INTEGER,
    relevance_why  TEXT,
    summarized_on  TEXT,
    reported_on    TEXT,
    llm            TEXT                -- 生成总结的模型提供方
);
CREATE INDEX IF NOT EXISTS idx_status ON papers(status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

JSON_FIELDS = ("authors", "methods", "applications")


class DB:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(papers)")}
        if "llm" not in cols:  # 兼容旧版本数据库
            self.conn.execute("ALTER TABLE papers ADD COLUMN llm TEXT")
            self.conn.commit()

    # ---------- meta ----------
    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- papers ----------
    def has(self, doi: str) -> bool:
        return self.conn.execute("SELECT 1 FROM papers WHERE doi=?", (doi.lower(),)).fetchone() is not None

    def insert(self, p: dict):
        p = dict(p)
        p["doi"] = p["doi"].lower()
        for f in JSON_FIELDS:
            if f in p and not isinstance(p[f], str):
                p[f] = json.dumps(p[f], ensure_ascii=False)
        cols = ",".join(p)
        qs = ",".join("?" * len(p))
        self.conn.execute(f"INSERT OR IGNORE INTO papers({cols}) VALUES({qs})", tuple(p.values()))
        self.conn.commit()

    def update(self, doi: str, **fields):
        for f in JSON_FIELDS:
            if f in fields and not isinstance(fields[f], str):
                fields[f] = json.dumps(fields[f], ensure_ascii=False)
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE papers SET {sets} WHERE doi=?", (*fields.values(), doi.lower()))
        self.conn.commit()

    def by_status(self, status: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM papers WHERE status=? ORDER BY journal, published DESC", (status,)
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for f in JSON_FIELDS:
            if d.get(f):
                try:
                    d[f] = json.loads(d[f])
                except json.JSONDecodeError:
                    d[f] = []
            else:
                d[f] = []
        return d
