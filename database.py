import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "arbitr_bot.db")


class Database:
    def __init__(self):
        self.path = DB_PATH

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self):
        """Создаёт таблицы если их нет"""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    inn TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    added_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, inn),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    inn TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                );
            """)

    def add_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,)
            )

    def get_user_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_total_inn_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(DISTINCT inn) FROM watchlist").fetchone()[0]

    def add_inn(self, user_id: int, inn: str, company_name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, inn, company_name) VALUES (?, ?, ?)",
                (user_id, inn, company_name)
            )

    def remove_inn(self, user_id: int, inn: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND inn = ?",
                (user_id, inn)
            )

    def inn_exists_for_user(self, user_id: int, inn: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE user_id = ? AND inn = ?",
                (user_id, inn)
            ).fetchone()
            return row is not None

    def get_company_name(self, inn: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT company_name FROM watchlist WHERE inn = ? LIMIT 1",
                (inn,)
            ).fetchone()
            return row[0] if row else inn

    def get_user_companies(self, user_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT inn, company_name FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
                (user_id,)
            ).fetchall()
            return [(r["inn"], r["company_name"]) for r in rows]

    def get_all_inns(self) -> list:
        """Возвращает все уникальные ИНН с названиями компаний"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT inn, company_name FROM watchlist"
            ).fetchall()
            return [(r["inn"], r["company_name"]) for r in rows]

    def get_users_for_inn(self, inn: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id FROM watchlist WHERE inn = ?",
                (inn,)
            ).fetchall()
            return [r["user_id"] for r in rows]

    def save_snapshot(self, inn: str, cases: list):
        """Сохраняет текущее состояние дел для ИНН"""
        data = json.dumps(cases, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (inn, data, updated_at) VALUES (?, ?, datetime('now'))",
                (inn, data)
            )

    def compare_and_update_snapshot(self, inn: str, current_cases: list) -> list:
        """
        Сравнивает текущие дела со снимком.
        Возвращает список изменений.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM snapshots WHERE inn = ?",
                (inn,)
            ).fetchone()

        if not row:
            # Первый раз — просто сохраняем, ничего не уведомляем
            self.save_snapshot(inn, current_cases)
            return []

        old_cases = json.loads(row["data"])
        changes = self._detect_changes(old_cases, current_cases)

        if changes:
            self.save_snapshot(inn, current_cases)

        return changes

    def _detect_changes(self, old_cases: list, new_cases: list) -> list:
        """Определяет что изменилось между двумя снимками"""
        changes = []

        old_by_id = {c["case_id"]: c for c in old_cases}
        new_by_id = {c["case_id"]: c for c in new_cases}

        # Новые дела
        for case_id, case in new_by_id.items():
            if case_id not in old_by_id:
                changes.append({
                    "type": "🆕 Подан новый иск",
                    "case_id": case_id,
                    "case_number": case.get("number", ""),
                    "court": case.get("court", ""),
                    "amount": case.get("amount", ""),
                    "plaintiff": case.get("plaintiff", ""),
                    "defendant": case.get("defendant", ""),
                    "detail": "Зарегистрировано новое арбитражное дело"
                })
                continue

            old = old_by_id[case_id]

            # Изменение статуса
            if case.get("status") != old.get("status"):
                changes.append({
                    "type": "📋 Изменён статус дела",
                    "case_id": case_id,
                    "case_number": case.get("number", ""),
                    "court": case.get("court", ""),
                    "amount": case.get("amount", ""),
                    "plaintiff": case.get("plaintiff", ""),
                    "defendant": case.get("defendant", ""),
                    "detail": f"{old.get('status', '—')} → {case.get('status', '—')}"
                })

            # Новые документы
            old_docs = set(old.get("documents", []))
            new_docs = set(case.get("documents", []))
            added_docs = new_docs - old_docs
            for doc in added_docs:
                changes.append({
                    "type": "📄 Опубликован новый документ",
                    "case_id": case_id,
                    "case_number": case.get("number", ""),
                    "court": case.get("court", ""),
                    "plaintiff": case.get("plaintiff", ""),
                    "defendant": case.get("defendant", ""),
                    "detail": doc
                })

            # Изменение даты заседания
            if case.get("next_hearing") != old.get("next_hearing"):
                old_date = old.get("next_hearing") or "не назначено"
                new_date = case.get("next_hearing") or "не назначено"
                changes.append({
                    "type": "📅 Изменена дата заседания",
                    "case_id": case_id,
                    "case_number": case.get("number", ""),
                    "court": case.get("court", ""),
                    "plaintiff": case.get("plaintiff", ""),
                    "defendant": case.get("defendant", ""),
                    "detail": f"Было: {old_date} → Стало: {new_date}"
                })

            # Изменение судьи
            if case.get("judge") != old.get("judge"):
                changes.append({
                    "type": "👨‍⚖️ Смена судьи",
                    "case_id": case_id,
                    "case_number": case.get("number", ""),
                    "court": case.get("court", ""),
                    "plaintiff": case.get("plaintiff", ""),
                    "defendant": case.get("defendant", ""),
                    "detail": f"{old.get('judge', '—')} → {case.get('judge', '—')}"
                })

        return changes
