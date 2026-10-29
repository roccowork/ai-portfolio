"""SQLite 小账本：每天的调用量和费用、访客配额、向量缓存。数据量很小，不需要单独的数据库服务。"""

import hashlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

# 按北京时间切换日期（用户和费用账单都在国内时区）
TZ = ZoneInfo("Asia/Shanghai")


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS usage (
                  day TEXT, feature TEXT, model TEXT,
                  calls INTEGER, tokens_in INTEGER, tokens_out INTEGER, cost REAL, saved_min REAL,
                  PRIMARY KEY (day, feature, model));
                CREATE TABLE IF NOT EXISTS quota (day TEXT, visitor TEXT, feature TEXT, n INTEGER,
                  PRIMARY KEY (day, visitor, feature));
                CREATE TABLE IF NOT EXISTS emb_cache (sha TEXT PRIMARY KEY, model TEXT, vec BLOB);
                CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY, ts TEXT, visitor TEXT,
                  name TEXT, contact TEXT, company TEXT, need TEXT, chat TEXT);
                """
            )

    # ── 费用 ──────────────────────────────────────────
    def record(self, feature: str, model: str, tokens_in: int, tokens_out: int, cost: float, saved_min: float = 0) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO usage VALUES (?,?,?,1,?,?,?,?)
                   ON CONFLICT(day, feature, model) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in,
                   tokens_out=tokens_out+excluded.tokens_out, cost=cost+excluded.cost, saved_min=saved_min+excluded.saved_min""",
                (today(), feature, model, tokens_in, tokens_out, cost, saved_min),
            )
            self._db.commit()

    def add_saved(self, feature: str, minutes: float) -> None:
        """一次请求成功完成后，记一笔「替你省下的时间」（估算）。"""
        with self._lock:
            self._db.execute(
                """INSERT INTO usage VALUES (?,?,'-',0,0,0,0,?)
                   ON CONFLICT(day, feature, model) DO UPDATE SET saved_min=saved_min+excluded.saved_min""",
                (today(), feature, minutes),
            )
            self._db.commit()

    def spent_today(self) -> float:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(cost),0) FROM usage WHERE day=?", (today(),)).fetchone()
        return float(row[0])

    def feature_usage(self, feature: str, since: str) -> dict:
        """某个功能从 since（YYYY-MM-DD，含）到今天的调用次数、token 和估算费用。"""
        r = self._db.execute(
            "SELECT COALESCE(SUM(calls),0), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), COALESCE(SUM(cost),0) FROM usage WHERE feature=? AND day>=?",
            (feature, since),
        ).fetchone()
        return {"calls": int(r[0]), "tokens": int(r[1]) + int(r[2]), "cost": round(float(r[3]), 4)}

    def summary(self) -> dict:
        with self._lock:
            def agg(where: str, args: tuple) -> dict:
                r = self._db.execute(
                    f"SELECT COALESCE(SUM(calls),0), COALESCE(SUM(cost),0), COALESCE(SUM(saved_min),0) FROM usage {where}", args
                ).fetchone()
                return {"calls": int(r[0]), "cost": round(float(r[1]), 4), "saved_min": round(float(r[2]), 1)}

            by_feature = {
                f: {"calls": int(c), "saved_min": round(float(s), 1)}
                for f, c, s in self._db.execute(
                    "SELECT feature, SUM(calls), SUM(saved_min) FROM usage GROUP BY feature"
                ).fetchall()
            }
            return {"today": agg("WHERE day=?", (today(),)), "total": agg("", ()), "by_feature": by_feature}

    # ── 访客配额 ──────────────────────────────────────
    @staticmethod
    def visitor_id(ip: str) -> str:
        # 不保存 IP：按天加盐后取哈希，第二天就无法关联
        return hashlib.sha256(f"{today()}|{ip}".encode()).hexdigest()[:16]

    def take_quota(self, ip: str, feature: str, limit: int) -> bool:
        """配额还有剩余就占用一次并返回 True。"""
        v = self.visitor_id(ip)
        with self._lock:
            row = self._db.execute("SELECT n FROM quota WHERE day=? AND visitor=? AND feature=?", (today(), v, feature)).fetchone()
            if row and row[0] >= limit:
                return False
            self._db.execute(
                "INSERT INTO quota VALUES (?,?,?,1) ON CONFLICT(day, visitor, feature) DO UPDATE SET n=n+1", (today(), v, feature)
            )
            self._db.commit()
        return True

    # ── 客户线索 ──────────────────────────────────────
    def add_lead(self, ip: str, name: str, contact: str, company: str, need: str, chat: str) -> int:
        """官网留资。访客 IP 只存哈希（和配额一样），联系方式是访客主动留下的"""
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO leads (ts, visitor, name, contact, company, need, chat) VALUES (?,?,?,?,?,?,?)",
                (datetime.now(TZ).isoformat(timespec="seconds"), self.visitor_id(ip), name, contact, company, need, chat),
            )
            self._db.commit()
        return int(cur.lastrowid or 0)

    def leads(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute("SELECT id, ts, name, contact, company, need FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("id", "ts", "name", "contact", "company", "need"), r, strict=True)) for r in rows]

    # ── 向量缓存 ──────────────────────────────────────
    def get_vectors(self, shas: list[str], model: str) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        with self._lock:
            for i in range(0, len(shas), 500):
                part = shas[i : i + 500]
                q = f"SELECT sha, vec FROM emb_cache WHERE model=? AND sha IN ({','.join('?' * len(part))})"
                for sha, blob in self._db.execute(q, (model, *part)).fetchall():
                    out[sha] = np.frombuffer(blob, dtype=np.float32)
        return out

    def put_vectors(self, items: dict[str, np.ndarray], model: str) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO emb_cache VALUES (?,?,?)",
                [(sha, model, vec.astype(np.float32).tobytes()) for sha, vec in items.items()],
            )
            self._db.commit()
