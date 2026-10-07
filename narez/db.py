"""SQLite: группы аккаунтов, аккаунты, задания, клипы и история публикаций."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

PLATFORMS = ["youtube", "tiktok", "instagram"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  platform TEXT NOT NULL, label TEXT DEFAULT '', credentials TEXT DEFAULT '{}',
  UNIQUE(group_id, platform));
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, group_id INTEGER REFERENCES groups(id), url TEXT, title TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
  path TEXT, start REAL, end REAL, title TEXT, reason TEXT, score REAL,
  status TEXT DEFAULT 'ready');
CREATE TABLE IF NOT EXISTS presets(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, data TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS proxies(
  id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, status TEXT DEFAULT 'new', ip TEXT DEFAULT '', country TEXT DEFAULT '',
  code TEXT DEFAULT '', city TEXT DEFAULT '', isp TEXT DEFAULT '', ms INTEGER DEFAULT 0, error TEXT DEFAULT '',
  checked_at TEXT DEFAULT '', active INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS deferred(
  id INTEGER PRIMARY KEY, clip_id INTEGER, account_id INTEGER, run_at TEXT NOT NULL,
  status TEXT DEFAULT 'wait', error TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS publications(
  id INTEGER PRIMARY KEY, clip_id INTEGER, account_id INTEGER, group_id INTEGER,
  platform TEXT, video_id TEXT, url TEXT, title TEXT, thumb TEXT DEFAULT '',
  publish_at TEXT DEFAULT '', uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
  views INTEGER, likes INTEGER, comments INTEGER, privacy TEXT DEFAULT '', stats_at TEXT DEFAULT '');
"""

# колонки, добавленные позже: старые базы дополняются автоматически
MIGRATIONS = {
    "jobs": {"source_path": "TEXT DEFAULT ''", "test": "INTEGER DEFAULT 0"},
    "clips": {"group_id": "INTEGER", "tags": "TEXT DEFAULT ''", "banner": "TEXT DEFAULT ''", "banner_applied": "TEXT DEFAULT ''",
              "final_path": "TEXT DEFAULT ''", "thumb": "TEXT DEFAULT ''", "publish_at": "TEXT DEFAULT ''"},
}
CLIP_FIELDS = {"group_id", "path", "title", "reason", "tags", "banner", "banner_applied", "final_path", "thumb", "status", "publish_at"}


class DB:
    def __init__(self, path: Path, init: bool = True):
        """init=False — не проверять схему заново (для коротких соединений веб-сервера после первого запуска)."""
        self.c = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys=ON")
        if init:
            self.c.executescript(SCHEMA)
            self._migrate()

    def _migrate(self):
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols.items():
                if name not in have:
                    self.c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self.c.commit()

    # --- группы и аккаунты ---
    def add_group(self, name: str) -> int:
        cur = self.c.execute("INSERT OR IGNORE INTO groups(name) VALUES(?)", (name,))
        self.c.commit()
        return cur.lastrowid or self.c.execute("SELECT id FROM groups WHERE name=?", (name,)).fetchone()["id"]

    def groups(self):
        return self.c.execute("SELECT * FROM groups ORDER BY name").fetchall()

    def group(self, group_id: int):
        return self.c.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()

    def account(self, account_id: int):
        return self.c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()

    def delete_account(self, account_id: int):
        self.c.execute("DELETE FROM deferred WHERE account_id=?", (account_id,))
        self.c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        self.c.commit()

    # --- отложенные публикации (Instagram/TikTok: время держит narez) ---
    def add_deferred(self, clip_id: int, account_id: int, run_at: str) -> int:
        cur = self.c.execute("INSERT INTO deferred(clip_id,account_id,run_at) VALUES(?,?,?)", (clip_id, account_id, run_at))
        self.c.commit()
        return cur.lastrowid

    def deferred_waiting(self, clip_id: int, account_id: int) -> bool:
        return bool(self.c.execute("SELECT 1 FROM deferred WHERE clip_id=? AND account_id=? AND status IN ('wait','run')",
                                   (clip_id, account_id)).fetchone())

    def deferred_list(self, include_done: bool = False):
        q = ("SELECT d.*, c.title AS clip_title, a.platform, a.label, g.name AS group_name FROM deferred d "
             "LEFT JOIN clips c ON c.id=d.clip_id LEFT JOIN accounts a ON a.id=d.account_id "
             "LEFT JOIN groups g ON g.id=a.group_id")
        if not include_done:
            q += " WHERE d.status IN ('wait','run','error')"
        return self.c.execute(q + " ORDER BY d.run_at").fetchall()

    def deferred_due(self, now_iso: str):
        return self.c.execute("SELECT * FROM deferred WHERE status='wait' AND run_at<=? ORDER BY run_at", (now_iso,)).fetchall()

    def set_deferred(self, did: int, status: str, error: str = ""):
        self.c.execute("UPDATE deferred SET status=?, error=? WHERE id=?", (status, error, did))
        self.c.commit()

    def delete_deferred(self, did: int):
        self.c.execute("DELETE FROM deferred WHERE id=?", (did,))
        self.c.commit()

    def publication(self, pub_id: int):
        return self.c.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()

    def set_account(self, group_id: int, platform: str, label: str, credentials: dict | None = None):
        self.c.execute(
            "INSERT INTO accounts(group_id,platform,label,credentials) VALUES(?,?,?,?) "
            "ON CONFLICT(group_id,platform) DO UPDATE SET label=excluded.label, credentials=excluded.credentials",
            (group_id, platform, label, json.dumps(credentials or {})))
        self.c.commit()

    def accounts(self, group_id: int):
        return self.c.execute("SELECT * FROM accounts WHERE group_id=?", (group_id,)).fetchall()

    # --- задания и клипы ---
    def add_job(self, group_id: int | None, url: str, title: str, source_path: str = "", test: bool = False) -> int:
        cur = self.c.execute("INSERT INTO jobs(group_id,url,title,source_path,test) VALUES(?,?,?,?,?)",
                             (group_id, url, title, source_path, int(test)))
        self.c.commit()
        return cur.lastrowid

    def add_clip(self, job_id: int, path: str, m, tags: str = "", banner: str = "", thumb: str = "") -> int:
        cur = self.c.execute(
            "INSERT INTO clips(job_id,path,start,end,title,reason,score,tags,banner,thumb) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, path, m.start, m.end, m.title, m.reason, m.score, tags, banner, thumb))
        self.c.commit()
        return cur.lastrowid

    def clips(self, job_id: int):
        return self.c.execute("SELECT * FROM clips WHERE job_id=? ORDER BY start", (job_id,)).fetchall()

    def clip(self, clip_id: int):
        return self.c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()

    def update_clip(self, clip_id: int, **fields):
        bad = set(fields) - CLIP_FIELDS
        if bad:
            raise ValueError(f"нельзя менять поля: {bad}")
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.c.execute(f"UPDATE clips SET {sets} WHERE id=?", (*fields.values(), clip_id))
            self.c.commit()

    def pending_clips(self, group_id: int):
        """Клипы группы, которые ещё ждут публикации (в порядке создания)."""
        return self.c.execute(
            "SELECT c.* FROM clips c JOIN jobs j ON j.id=c.job_id "
            "WHERE COALESCE(c.group_id, j.group_id)=? AND c.status='ready' ORDER BY c.id", (group_id,)).fetchall()

    def pending_all(self):
        """Все клипы, ждущие публикации, из всех групп."""
        return self.c.execute("SELECT * FROM clips WHERE status='ready' ORDER BY id").fetchall()

    def clip_group(self, clip) -> int | None:
        """Группа клипа: выбранная в редакторе или группа задания."""
        if clip["group_id"]:
            return clip["group_id"]
        job = self.job(clip["job_id"])
        return job["group_id"] if job else None

    def open_clip_count(self, job_id: int) -> int:
        return self.c.execute("SELECT COUNT(*) FROM clips WHERE job_id=? AND status='ready'", (job_id,)).fetchone()[0]

    def clip_count(self, job_id: int) -> int:
        return self.c.execute("SELECT COUNT(*) FROM clips WHERE job_id=?", (job_id,)).fetchone()[0]

    def jobs_with_source(self, group_id: int):
        return self.c.execute("SELECT * FROM jobs WHERE (?1 IS NULL OR group_id=?1) AND source_path!='' AND COALESCE(test,0)=0", (group_id,)).fetchall()

    # --- прокси ---
    def proxies(self):
        return self.c.execute("SELECT * FROM proxies ORDER BY active DESC, (status='ok') DESC, id").fetchall()

    def proxy(self, proxy_id: int):
        return self.c.execute("SELECT * FROM proxies WHERE id=?", (proxy_id,)).fetchone()

    def add_proxy(self, url: str) -> int | None:
        cur = self.c.execute("INSERT OR IGNORE INTO proxies(url) VALUES(?)", (url,))
        self.c.commit()
        return cur.lastrowid if cur.rowcount else None

    def set_proxy_result(self, proxy_id: int, res: dict):
        self.c.execute("UPDATE proxies SET status=?, ip=?, country=?, code=?, city=?, isp=?, ms=?, error=?, "
                       "checked_at=datetime('now') WHERE id=?",
                       ("ok" if res["ok"] else "bad", res["ip"], res["country"], res["code"], res["city"], res["isp"],
                        res["ms"], res["error"], proxy_id))
        self.c.commit()

    def set_active_proxy(self, proxy_id: int | None):
        self.c.execute("UPDATE proxies SET active=0")
        if proxy_id:
            self.c.execute("UPDATE proxies SET active=1 WHERE id=?", (proxy_id,))
        self.c.commit()

    def active_proxy(self):
        return self.c.execute("SELECT * FROM proxies WHERE active=1").fetchone()

    def delete_proxies(self, ids: list[int]):
        self.c.executemany("DELETE FROM proxies WHERE id=?", [(i,) for i in ids])
        self.c.commit()

    # --- пресеты редактирования ---
    def presets(self):
        return self.c.execute("SELECT * FROM presets ORDER BY name COLLATE NOCASE").fetchall()

    def preset(self, preset_id: int):
        return self.c.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()

    def save_preset(self, name: str, data: dict) -> int:
        self.c.execute("INSERT INTO presets(name,data) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                       (name, json.dumps(data, ensure_ascii=False)))
        self.c.commit()
        return self.c.execute("SELECT id FROM presets WHERE name=?", (name,)).fetchone()["id"]

    def delete_preset(self, preset_id: int):
        self.c.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        self.c.commit()

    def job(self, job_id: int):
        return self.c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def clear_source(self, job_id: int):
        self.c.execute("UPDATE jobs SET source_path='' WHERE id=?", (job_id,))
        self.c.commit()

    # --- публикации (история) ---
    def add_publication(self, clip_id: int, account_id: int, group_id: int | None, platform: str, url: str,
                        title: str, thumb: str = "", publish_at: str = "", uploaded_at: str | None = None) -> int:
        cur = self.c.execute(
            "INSERT INTO publications(clip_id,account_id,group_id,platform,video_id,url,title,thumb,publish_at,uploaded_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,COALESCE(?, CURRENT_TIMESTAMP))",
            (clip_id, account_id, group_id, platform, url.rstrip("/").rsplit("/", 1)[-1], url, title, thumb, publish_at,
             uploaded_at))
        self.c.commit()
        return cur.lastrowid

    def clip_published_accounts(self, clip_id: int) -> set[int]:
        return {r["account_id"] for r in
                self.c.execute("SELECT account_id FROM publications WHERE clip_id=?", (clip_id,))}

    def known_video_ids(self, platform: str = "youtube") -> set[str]:
        return {r["video_id"] for r in self.c.execute("SELECT video_id FROM publications WHERE platform=?", (platform,))}

    def publications(self, platform: str | None = None):
        q = ("SELECT p.*, g.name AS group_name, a.label AS account_label FROM publications p "
             "LEFT JOIN groups g ON g.id=p.group_id LEFT JOIN accounts a ON a.id=p.account_id")
        args: tuple = ()
        if platform:
            q += " WHERE p.platform=?"
            args = (platform,)
        return self.c.execute(q + " ORDER BY p.id DESC", args).fetchall()

    def update_stats(self, pub_id: int, views: int | None, likes: int | None, comments: int | None,
                     privacy: str, publish_at: str | None = None):
        self.c.execute(
            "UPDATE publications SET views=?, likes=?, comments=?, privacy=?, stats_at=datetime('now'), "
            "publish_at=COALESCE(?, publish_at) WHERE id=?",
            (views, likes, comments, privacy, publish_at, pub_id))
        self.c.commit()
