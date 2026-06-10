from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# Local cache. In Google Sheets mode this file is only a temporary working copy;
# Google Sheets remains the persistent source of truth.
DB_PATH = Path(os.getenv("PORRA_DB_PATH", str(Path(tempfile.gettempdir()) / "porra_martinotes.db")))

SHEETS_TABLES = [
    "tournaments",
    "rounds",
    "participants",
    "teams",
    "matches",
    "predictions",
    "ranking_overrides",
    "scoring_rules",
    "bracket_predictions",
    "extra_predictions",
    "extra_validations",
]

_SHEETS_LOADED = False
_SYNCING_TO_SHEETS = False
_DEFER_SHEETS_SYNC = False
_SHEETS_DIRTY = False
_SHEETS_DIRTY_TABLES: set[str] = set()
_GSPREAD_CLIENT = None
_SPREADSHEET = None
DEFAULT_RULES: dict[str, float] = {
    "base_points": 1000.0,
    "global_groups_weight": 40.0,
    "global_knockout_weight": 50.0,
    "global_extras_weight": 10.0,
    "group_positions_weight": 70.0,
    "group_sign_weight": 25.0,
    "group_exact_weight": 5.0,
    "knockout_qualifier_weight": 70.0,
    "knockout_result_weight": 30.0,
    "extra_time_multiplier": 0.5,
    "penalties_multiplier": 0.5,
    "bonus_octavos": 1.0,
    "bonus_cuartos": 3.0,
    "bonus_semifinales": 6.0,
    "bonus_final": 12.0,
    "bonus_campeon": 25.0,
    "extra_balon_oro": 25.0,
    "extra_bota_oro": 15.0,
    "extra_guante_oro": 15.0,
    "extra_mejor_joven": 15.0,
    "extra_equipo_entretenido": 15.0,
    "extra_gol_torneo": 15.0,
}

ROUND_ORDER = {
    "grupos": 1,
    "ronda32": 2,
    "octavos": 3,
    "cuartos": 4,
    "semifinales": 5,
    "final": 6,
}




_SCHEMA_SQL = r"""

            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                join_code TEXT NOT NULL UNIQUE,
                admin_pin TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_key TEXT NOT NULL,
                round_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                lock_datetime TEXT,
                UNIQUE(tournament_id, round_key),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                pin TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, name),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                group_letter TEXT NOT NULL,
                manual_tiebreak_order INTEGER,
                UNIQUE(tournament_id, name),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_key TEXT NOT NULL DEFAULT 'grupos',
                phase TEXT NOT NULL DEFAULT 'grupos',
                group_letter TEXT,
                matchday INTEGER,
                bracket_slot TEXT,
                home_team_id INTEGER NOT NULL,
                away_team_id INTEGER NOT NULL,
                kickoff_datetime TEXT,
                home_goals INTEGER,
                away_goals INTEGER,
                winner_team_id INTEGER,
                extra_time INTEGER NOT NULL DEFAULT 0,
                penalties INTEGER NOT NULL DEFAULT 0,
                resolution TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                origin TEXT NOT NULL DEFAULT 'manual',
                manual_tiebreak_order INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(home_team_id) REFERENCES teams(id),
                FOREIGN KEY(away_team_id) REFERENCES teams(id),
                FOREIGN KEY(winner_team_id) REFERENCES teams(id)
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                match_id INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT 'initial',
                round_key TEXT NOT NULL DEFAULT 'grupos',
                predicted_home_goals INTEGER,
                predicted_away_goals INTEGER,
                predicted_winner_team_id INTEGER,
                predicted_extra_time INTEGER NOT NULL DEFAULT 0,
                predicted_penalties INTEGER NOT NULL DEFAULT 0,
                is_original_path INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, match_id, scope),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(participant_id) REFERENCES participants(id) ON DELETE CASCADE,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
                FOREIGN KEY(predicted_winner_team_id) REFERENCES teams(id)
            );

            CREATE TABLE IF NOT EXISTS ranking_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                group_letter TEXT,
                team_id INTEGER NOT NULL,
                manual_order INTEGER NOT NULL,
                UNIQUE(tournament_id, scope, group_letter, team_id),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scoring_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value REAL NOT NULL,
                UNIQUE(tournament_id, rule_key),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );
            
            CREATE TABLE IF NOT EXISTS bracket_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT 'initial',
                round_key TEXT NOT NULL,
                slot INTEGER NOT NULL,
                home_team_id INTEGER NOT NULL,
                away_team_id INTEGER NOT NULL,
                predicted_home_goals INTEGER,
                predicted_away_goals INTEGER,
                predicted_winner_team_id INTEGER,
                predicted_extra_time INTEGER NOT NULL DEFAULT 0,
                predicted_penalties INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, scope, round_key, slot),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(participant_id) REFERENCES participants(id) ON DELETE CASCADE,
                FOREIGN KEY(home_team_id) REFERENCES teams(id),
                FOREIGN KEY(away_team_id) REFERENCES teams(id),
                FOREIGN KEY(predicted_winner_team_id) REFERENCES teams(id)
            );

            CREATE TABLE IF NOT EXISTS extra_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                extra_key TEXT NOT NULL,
                predicted_value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, extra_key),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(participant_id) REFERENCES participants(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS extra_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                extra_key TEXT NOT NULL,
                is_correct INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, extra_key),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(participant_id) REFERENCES participants(id) ON DELETE CASCADE
            );

"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    _add_column_if_missing(conn, "matches", "extra_time", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "matches", "penalties", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "predictions", "predicted_extra_time", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "predictions", "predicted_penalties", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "predictions", "is_original_path", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "predictions", "locked_at", "TEXT")


def _streamlit_secrets() -> dict[str, Any]:
    try:
        import streamlit as st
        return dict(st.secrets)
    except Exception:
        return {}


def _google_sheet_name() -> str:
    secrets = _streamlit_secrets()
    return str(os.getenv("GOOGLE_SHEET_NAME") or secrets.get("GOOGLE_SHEET_NAME") or "porra_martinotes_db")


def _service_account_info() -> dict[str, Any] | None:
    secrets = _streamlit_secrets()

    if "gcp_service_account" in secrets:
        data = secrets["gcp_service_account"]
        return dict(data) if not isinstance(data, dict) else data

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return json.loads(raw)

    credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE")
    if credentials_file and Path(credentials_file).exists():
        return json.loads(Path(credentials_file).read_text(encoding="utf-8"))

    local_json = Path("service_account.json")
    if local_json.exists():
        return json.loads(local_json.read_text(encoding="utf-8"))

    return None


def sheets_enabled() -> bool:
    forced = str(os.getenv("USE_GOOGLE_SHEETS", "")).strip().lower()
    if forced in {"0", "false", "no", "off"}:
        return False
    return _service_account_info() is not None


def _local_db_path() -> Path:
    if not sheets_enabled():
        return DB_PATH
    return Path(tempfile.gettempdir()) / "porra_martinotes_sheets_cache.db"


def _get_spreadsheet():
    global _GSPREAD_CLIENT, _SPREADSHEET
    if _SPREADSHEET is not None:
        return _SPREADSHEET

    import gspread
    from google.oauth2.service_account import Credentials

    info = _service_account_info()
    if not info:
        raise RuntimeError("No se han encontrado credenciales de Google Sheets.")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    _GSPREAD_CLIENT = gspread.authorize(creds)
    _SPREADSHEET = _GSPREAD_CLIENT.open(_google_sheet_name())
    return _SPREADSHEET


def _worksheet_map(spreadsheet) -> dict[str, Any]:
    return {ws.title: ws for ws in spreadsheet.worksheets()}


def _sqlite_type_map(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: str(row[2]).upper() for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _convert_cell(value: Any, declared_type: str) -> Any:
    if value == "" or value is None:
        return None
    if "INT" in declared_type:
        try:
            return int(float(value))
        except Exception:
            return None
    if "REAL" in declared_type or "FLOA" in declared_type or "DOUB" in declared_type:
        try:
            return float(value)
        except Exception:
            return None
    return str(value)


def _load_sheets_into_sqlite(conn: sqlite3.Connection) -> None:
    spreadsheet = _get_spreadsheet()
    worksheets = _worksheet_map(spreadsheet)
    _create_schema(conn)

    for table in SHEETS_TABLES:
        if table not in worksheets:
            continue
        values = worksheets[table].get_all_values()
        if not values:
            continue
        headers = [str(h).strip() for h in values[0] if str(h).strip()]
        if not headers:
            continue
        table_cols = _columns(conn, table)
        headers = [h for h in headers if h in table_cols]
        if not headers:
            continue

        conn.execute(f"DELETE FROM {table}")
        type_map = _sqlite_type_map(conn, table)
        placeholders = ", ".join(["?"] * len(headers))
        col_sql = ", ".join(headers)
        insert_sql = f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})"

        rows_to_insert = []
        required_not_null = {
            "tournaments": ["name", "join_code", "admin_pin"],
            "rounds": ["tournament_id", "round_key", "round_name", "status"],
            "participants": ["tournament_id", "name", "pin"],
            "teams": ["tournament_id", "name", "group_letter"],
            "matches": ["tournament_id", "round_key", "phase", "home_team_id", "away_team_id", "status", "origin"],
            "predictions": ["tournament_id", "participant_id", "match_id", "scope", "round_key"],
            "ranking_overrides": ["tournament_id", "scope", "team_id", "manual_order"],
            "scoring_rules": ["tournament_id", "rule_key", "rule_value"],
            "bracket_predictions": ["tournament_id", "participant_id", "scope", "round_key", "slot", "home_team_id", "away_team_id"],
            "extra_predictions": ["tournament_id", "participant_id", "extra_key"],
            "extra_validations": ["tournament_id", "participant_id", "extra_key", "is_correct"],
        }
        required_idx = [headers.index(col) for col in required_not_null.get(table, []) if col in headers]
        for raw_row in values[1:]:
            row_dict = {str(k).strip(): v for k, v in dict(zip(values[0], raw_row)).items()}
            converted = [_convert_cell(row_dict.get(col, ""), type_map.get(col, "TEXT")) for col in headers]
            if not any(v is not None for v in converted):
                continue
            if any(converted[i] is None for i in required_idx):
                continue
            rows_to_insert.append(converted)
        if rows_to_insert:
            conn.executemany(insert_sql, rows_to_insert)


def _extract_modified_tables(sql: str) -> set[str]:
    """Best-effort extraction of tables modified by a simple SQL statement.

    This is intentionally conservative: if the statement is complex and cannot be
    parsed, the caller can still fall back to a full sync. The goal is to avoid
    writing every worksheet after every player save.
    """
    text = " ".join(str(sql).replace("\n", " ").split())
    lower = text.lower()
    tables: set[str] = set()

    patterns = (
        "insert into ",
        "insert or replace into ",
        "insert or ignore into ",
        "replace into ",
        "update ",
        "delete from ",
    )
    for pattern in patterns:
        idx = lower.find(pattern)
        if idx == -1:
            continue
        rest = text[idx + len(pattern):].strip()
        if not rest:
            continue
        table = rest.split()[0].strip('`"[]')
        table = table.split("(")[0].strip('`"[]')
        if table in SHEETS_TABLES:
            tables.add(table)
    return tables


def _set_dirty_tables(tables: set[str] | None = None) -> None:
    global _SHEETS_DIRTY, _SHEETS_DIRTY_TABLES
    _SHEETS_DIRTY = True
    if tables:
        _SHEETS_DIRTY_TABLES.update(tables)
    else:
        # Unknown write path: safest fallback is a full sync.
        _SHEETS_DIRTY_TABLES.update(SHEETS_TABLES)


def _sync_sqlite_to_sheets(conn: sqlite3.Connection, tables: set[str] | None = None) -> None:
    global _SYNCING_TO_SHEETS
    if _SYNCING_TO_SHEETS or not sheets_enabled():
        return
    _SYNCING_TO_SHEETS = True
    try:
        spreadsheet = _get_spreadsheet()
        worksheets = _worksheet_map(spreadsheet)
        target_tables = [t for t in SHEETS_TABLES if tables is None or t in tables]

        for table in target_tables:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if not columns:
                continue
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            values = [columns]
            for row in rows:
                values.append(["" if row[col] is None else row[col] for col in columns])

            if table in worksheets:
                ws = worksheets[table]
                # Clear only the worksheet that really changed. Previously the app
                # cleared and rewrote every worksheet on every save, which quickly
                # exhausted the Google Sheets write quota in Streamlit Cloud.
                ws.clear()
            else:
                ws = spreadsheet.add_worksheet(title=table, rows=max(len(values), 50), cols=max(len(columns), 10))
            ws.update(values, "A1", value_input_option="RAW")
    finally:
        _SYNCING_TO_SHEETS = False


def _safe_sync_sqlite_to_sheets(conn: sqlite3.Connection, tables: set[str] | None = None) -> bool:
    """Try to persist SQLite to Google Sheets without crashing the Streamlit run.

    Google Sheets has a strict per-minute write quota. If the quota is hit, the
    local SQLite write has already succeeded, so the best user experience is to
    keep the app alive and retry on a later write/reload instead of showing a red
    exception screen.
    """
    try:
        _sync_sqlite_to_sheets(conn, tables=tables)
        return True
    except Exception as exc:
        print(f"Google Sheets sync skipped: {type(exc).__name__}: {exc}")
        return False

def _ensure_sheets_loaded() -> None:
    global _SHEETS_LOADED
    if not sheets_enabled() or _SHEETS_LOADED:
        return
    path = _local_db_path()
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        _load_sheets_into_sqlite(conn)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()
    _SHEETS_LOADED = True


@contextmanager
def get_conn(sync: bool = True):
    global _SHEETS_DIRTY, _SHEETS_DIRTY_TABLES
    if sheets_enabled():
        _ensure_sheets_loaded()
    conn = sqlite3.connect(_local_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    changes_before = conn.total_changes
    try:
        yield conn
        conn.commit()
        changed = conn.total_changes > changes_before
        if sheets_enabled() and changed and sync:
            if _DEFER_SHEETS_SYNC:
                _set_dirty_tables(None)
            else:
                _safe_sync_sqlite_to_sheets(conn, tables=None)
    finally:
        conn.close()


@contextmanager
def defer_sheets_sync():
    global _DEFER_SHEETS_SYNC, _SHEETS_DIRTY, _SHEETS_DIRTY_TABLES
    previous_defer = _DEFER_SHEETS_SYNC
    previous_dirty = _SHEETS_DIRTY
    previous_dirty_tables = set(_SHEETS_DIRTY_TABLES)
    _DEFER_SHEETS_SYNC = True
    _SHEETS_DIRTY = False
    _SHEETS_DIRTY_TABLES = set()
    try:
        yield
    finally:
        dirty_now = _SHEETS_DIRTY
        dirty_tables_now = set(_SHEETS_DIRTY_TABLES)
        should_sync = sheets_enabled() and (not previous_defer) and dirty_now
        _DEFER_SHEETS_SYNC = previous_defer
        _SHEETS_DIRTY = previous_dirty or (dirty_now and previous_defer)
        _SHEETS_DIRTY_TABLES = previous_dirty_tables | (dirty_tables_now if previous_defer else set())
        if should_sync:
            with sqlite3.connect(_local_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                ok = _safe_sync_sqlite_to_sheets(conn, tables=dirty_tables_now or None)
            if ok:
                _SHEETS_DIRTY = previous_dirty
                _SHEETS_DIRTY_TABLES = previous_dirty_tables
            else:
                # Keep the dirty set in memory so the next write can retry.
                _SHEETS_DIRTY = True
                _SHEETS_DIRTY_TABLES = previous_dirty_tables | dirty_tables_now

def init_db() -> None:
    with get_conn() as conn:
        _create_schema(conn)


def query_df(sql: str, params: Iterable[Any] | None = None) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params or [])


def execute(sql: str, params: Iterable[Any] | None = None) -> None:
    with get_conn(sync=False) as conn:
        changes_before = conn.total_changes
        conn.execute(sql, tuple(params or []))
        changed = conn.total_changes > changes_before
    if sheets_enabled() and changed:
        tables = _extract_modified_tables(sql)
        if _DEFER_SHEETS_SYNC:
            _set_dirty_tables(tables or None)
        else:
            with sqlite3.connect(_local_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                _safe_sync_sqlite_to_sheets(conn, tables=tables or None)


def execute_many(sql: str, rows: list[Iterable[Any]]) -> None:
    with get_conn(sync=False) as conn:
        changes_before = conn.total_changes
        conn.executemany(sql, rows)
        changed = conn.total_changes > changes_before
    if sheets_enabled() and changed:
        tables = _extract_modified_tables(sql)
        if _DEFER_SHEETS_SYNC:
            _set_dirty_tables(tables or None)
        else:
            with sqlite3.connect(_local_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                _safe_sync_sqlite_to_sheets(conn, tables=tables or None)


def get_one(sql: str, params: Iterable[Any] | None = None) -> sqlite3.Row | None:
    with get_conn() as conn:
        cur = conn.execute(sql, tuple(params or []))
        return cur.fetchone()


def get_tournament_by_code(join_code: str) -> sqlite3.Row | None:
    return get_one("SELECT * FROM tournaments WHERE UPPER(join_code)=UPPER(?)", [join_code.strip()])

def create_tournament(name: str, join_code: str, admin_pin: str) -> int:
    clean_join_code = join_code.upper().strip()
    clean_admin_pin = admin_pin.strip()

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tournaments(name, join_code, admin_pin)
            VALUES (?, ?, ?)
            ON CONFLICT(join_code) DO UPDATE SET
                name = excluded.name,
                admin_pin = excluded.admin_pin
            RETURNING id
            """,
            (name, clean_join_code, clean_admin_pin),
        )

        tournament_id = int(cur.fetchone()[0])

        for k, v in DEFAULT_RULES.items():
            conn.execute(
                """
                INSERT INTO scoring_rules(tournament_id, rule_key, rule_value)
                VALUES (?, ?, ?)
                ON CONFLICT(tournament_id, rule_key) DO UPDATE SET
                    rule_value = excluded.rule_value
                """,
                (tournament_id, k, float(v)),
            )

        return tournament_id

def ensure_default_rules(tournament_id: int) -> None:
    for k, v in DEFAULT_RULES.items():
        execute(
            """
            INSERT INTO scoring_rules(tournament_id, rule_key, rule_value)
            VALUES (?, ?, ?)
            ON CONFLICT(tournament_id, rule_key) DO NOTHING
            """,
            [tournament_id, k, float(v)],
        )


def get_rules(tournament_id: int) -> dict[str, float]:
    ensure_default_rules(tournament_id)
    df = query_df("SELECT rule_key, rule_value FROM scoring_rules WHERE tournament_id=?", [tournament_id])
    rules = DEFAULT_RULES.copy()
    for _, row in df.iterrows():
        rules[str(row["rule_key"])] = float(row["rule_value"])
    return rules


def set_rule(tournament_id: int, rule_key: str, rule_value: float) -> None:
    execute(
        """
        INSERT INTO scoring_rules(tournament_id, rule_key, rule_value)
        VALUES (?, ?, ?)
        ON CONFLICT(tournament_id, rule_key) DO UPDATE SET rule_value=excluded.rule_value
        """,
        [tournament_id, rule_key, float(rule_value)],
    )


def upsert_round(tournament_id: int, round_key: str, round_name: str, status: str, lock_datetime: str | None) -> None:
    execute(
        """
        INSERT INTO rounds(tournament_id, round_key, round_name, status, lock_datetime)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tournament_id, round_key) DO UPDATE SET
            round_name=excluded.round_name,
            status=excluded.status,
            lock_datetime=excluded.lock_datetime
        """,
        [tournament_id, round_key, round_name, status, lock_datetime],
    )


def register_or_login_participant(tournament_id: int, name: str, pin: str) -> tuple[bool, str, int | None]:
    clean_name = name.strip()
    clean_pin = pin.strip()
    if not clean_name or not clean_pin:
        return False, "Introduce nombre y PIN.", None
    existing = get_one(
        "SELECT * FROM participants WHERE tournament_id=? AND LOWER(name)=LOWER(?)",
        [tournament_id, clean_name],
    )
    if existing:
        if existing["pin"] == clean_pin:
            return True, "Acceso correcto.", int(existing["id"])
        return False, "Ese nombre ya existe, pero el PIN no coincide.", None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO participants(tournament_id, name, pin) VALUES (?, ?, ?)",
            (tournament_id, clean_name, clean_pin),
        )
        return True, "Usuario creado.", int(cur.lastrowid)


def add_team(tournament_id: int, name: str, group_letter: str) -> int:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO teams(tournament_id, name, group_letter)
            VALUES (?, ?, ?)
            ON CONFLICT(tournament_id, name) DO UPDATE SET group_letter=excluded.group_letter
            """,
            (tournament_id, name.strip(), group_letter.strip().upper()),
        )
        row = conn.execute("SELECT id FROM teams WHERE tournament_id=? AND name=?", (tournament_id, name.strip())).fetchone()
        return int(row["id"])


def get_team_id(tournament_id: int, team_name: str) -> int | None:
    row = get_one("SELECT id FROM teams WHERE tournament_id=? AND name=?", [tournament_id, team_name])
    return int(row["id"]) if row else None


def add_match(
    tournament_id: int,
    round_key: str,
    phase: str,
    group_letter: str | None,
    matchday: int | None,
    home_team_id: int,
    away_team_id: int,
    kickoff_datetime: str | None = None,
    origin: str = "manual",
    bracket_slot: str | None = None,
) -> None:
    execute(
        """
        INSERT INTO matches(
            tournament_id, round_key, phase, group_letter, matchday,
            home_team_id, away_team_id, kickoff_datetime, origin, bracket_slot
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [tournament_id, round_key, phase, group_letter, matchday, home_team_id, away_team_id, kickoff_datetime, origin, bracket_slot],
    )


def upsert_prediction(
    tournament_id: int,
    participant_id: int,
    match_id: int,
    predicted_home_goals: int | None,
    predicted_away_goals: int | None,
    scope: str,
    round_key: str,
    predicted_winner_team_id: int | None = None,
    predicted_extra_time: bool = False,
    predicted_penalties: bool = False,
    is_original_path: bool = False,
) -> None:
    execute(
        """
        INSERT INTO predictions(
            tournament_id, participant_id, match_id, scope, round_key,
            predicted_home_goals, predicted_away_goals, predicted_winner_team_id,
            predicted_extra_time, predicted_penalties, is_original_path, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tournament_id, participant_id, match_id, scope) DO UPDATE SET
            predicted_home_goals=excluded.predicted_home_goals,
            predicted_away_goals=excluded.predicted_away_goals,
            predicted_winner_team_id=excluded.predicted_winner_team_id,
            predicted_extra_time=excluded.predicted_extra_time,
            predicted_penalties=excluded.predicted_penalties,
            is_original_path=excluded.is_original_path,
            updated_at=CURRENT_TIMESTAMP
        """,
        [
            tournament_id,
            participant_id,
            match_id,
            scope,
            round_key,
            predicted_home_goals,
            predicted_away_goals,
            predicted_winner_team_id,
            1 if predicted_extra_time else 0,
            1 if predicted_penalties else 0,
            1 if is_original_path else 0,
        ],
    )


def update_match_result(
    match_id: int,
    home_goals: int | None,
    away_goals: int | None,
    winner_team_id: int | None = None,
    extra_time: bool = False,
    penalties: bool = False,
    resolution: str | None = None,
) -> None:
    status = "played" if home_goals is not None and away_goals is not None else "pending"
    execute(
        """
        UPDATE matches
        SET home_goals=?, away_goals=?, winner_team_id=?, extra_time=?, penalties=?, resolution=?, status=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        [home_goals, away_goals, winner_team_id, 1 if extra_time else 0, 1 if penalties else 0, resolution, status, match_id],
    )


def delete_match(match_id: int, tournament_id: int) -> None:
    execute("DELETE FROM matches WHERE id=? AND tournament_id=?", [match_id, tournament_id])


def update_match_meta(match_id: int, home_team_id: int, away_team_id: int, kickoff_datetime: str | None, origin: str = "editado") -> None:
    execute(
        """
        UPDATE matches
        SET home_team_id=?, away_team_id=?, kickoff_datetime=?, origin=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        [home_team_id, away_team_id, kickoff_datetime, origin, match_id],
    )


def update_team(team_id: int, name: str, group_letter: str) -> None:
    execute("UPDATE teams SET name=?, group_letter=? WHERE id=?", [name.strip(), group_letter.strip().upper(), team_id])


def set_ranking_override(tournament_id: int, scope: str, group_letter: str | None, team_id: int, manual_order: int) -> None:
    # SQLite UNIQUE constraints do not treat NULL values as equal, so an UPSERT on
    # (scope, group_letter, team_id) is unreliable when group_letter is NULL
    # (used for the third-place ranking). Delete + insert is deterministic.
    with get_conn() as conn:
        if group_letter is None:
            conn.execute(
                """
                DELETE FROM ranking_overrides
                WHERE tournament_id=? AND scope=? AND group_letter IS NULL AND team_id=?
                """,
                (tournament_id, scope, team_id),
            )
        else:
            conn.execute(
                """
                DELETE FROM ranking_overrides
                WHERE tournament_id=? AND scope=? AND group_letter=? AND team_id=?
                """,
                (tournament_id, scope, group_letter, team_id),
            )
        conn.execute(
            """
            INSERT INTO ranking_overrides(tournament_id, scope, group_letter, team_id, manual_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tournament_id, scope, group_letter, team_id, manual_order),
        )


def upsert_bracket_prediction(
    tournament_id: int,
    participant_id: int,
    scope: str,
    round_key: str,
    slot: int,
    home_team_id: int,
    away_team_id: int,
    predicted_home_goals: int | None = None,
    predicted_away_goals: int | None = None,
    predicted_winner_team_id: int | None = None,
    predicted_extra_time: bool = False,
    predicted_penalties: bool = False,
) -> None:
    execute(
        """
        INSERT INTO bracket_predictions(
            tournament_id, participant_id, scope, round_key, slot, home_team_id, away_team_id,
            predicted_home_goals, predicted_away_goals, predicted_winner_team_id,
            predicted_extra_time, predicted_penalties, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tournament_id, participant_id, scope, round_key, slot) DO UPDATE SET
            home_team_id=excluded.home_team_id,
            away_team_id=excluded.away_team_id,
            predicted_home_goals=excluded.predicted_home_goals,
            predicted_away_goals=excluded.predicted_away_goals,
            predicted_winner_team_id=excluded.predicted_winner_team_id,
            predicted_extra_time=excluded.predicted_extra_time,
            predicted_penalties=excluded.predicted_penalties,
            updated_at=CURRENT_TIMESTAMP
        """,
        [
            tournament_id, participant_id, scope, round_key, int(slot), int(home_team_id), int(away_team_id),
            predicted_home_goals, predicted_away_goals, predicted_winner_team_id,
            1 if predicted_extra_time else 0, 1 if predicted_penalties else 0,
        ],
    )


def clear_bracket_round(tournament_id: int, participant_id: int, scope: str, round_key: str) -> None:
    execute(
        "DELETE FROM bracket_predictions WHERE tournament_id=? AND participant_id=? AND scope=? AND round_key=?",
        [tournament_id, participant_id, scope, round_key],
    )


def upsert_extra_prediction(tournament_id: int, participant_id: int, extra_key: str, predicted_value: str) -> None:
    execute(
        """
        INSERT INTO extra_predictions(tournament_id, participant_id, extra_key, predicted_value, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tournament_id, participant_id, extra_key) DO UPDATE SET
            predicted_value=excluded.predicted_value, updated_at=CURRENT_TIMESTAMP
        """,
        [tournament_id, participant_id, extra_key, predicted_value.strip()],
    )


def set_extra_validation(tournament_id: int, participant_id: int, extra_key: str, is_correct: bool) -> None:
    execute(
        """
        INSERT INTO extra_validations(tournament_id, participant_id, extra_key, is_correct, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tournament_id, participant_id, extra_key) DO UPDATE SET
            is_correct=excluded.is_correct, updated_at=CURRENT_TIMESTAMP
        """,
        [tournament_id, participant_id, extra_key, 1 if is_correct else 0],
    )


def seed_default_tournament() -> int:
    from seed_data import DEFAULT_ADMIN_PIN, DEFAULT_ROUNDS, DEFAULT_TOURNAMENT_CODE, GENERIC_GROUP_PAIRINGS, GROUPS

    def _seed() -> int:
        existing = get_tournament_by_code(DEFAULT_TOURNAMENT_CODE)
        if existing:
            tournament_id = int(existing["id"])
            # Production guard: do not rewrite Sheets on every page load.
            # Only insert defaults that are genuinely missing.
            with get_conn() as conn:
                for k, v in DEFAULT_RULES.items():
                    conn.execute(
                        """
                        INSERT INTO scoring_rules(tournament_id, rule_key, rule_value)
                        VALUES (?, ?, ?)
                        ON CONFLICT(tournament_id, rule_key) DO NOTHING
                        """,
                        (tournament_id, k, float(v)),
                    )
                for round_key, round_name, status, lock_datetime in DEFAULT_ROUNDS:
                    conn.execute(
                        """
                        INSERT INTO rounds(tournament_id, round_key, round_name, status, lock_datetime)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(tournament_id, round_key) DO NOTHING
                        """,
                        (tournament_id, round_key, round_name, status, lock_datetime),
                    )
                conn.execute("DELETE FROM rounds WHERE tournament_id=? AND round_key IN ('r32', 'semis')", (tournament_id,))
            return tournament_id

        tournament_id = create_tournament("Porra Martinotes", DEFAULT_TOURNAMENT_CODE, DEFAULT_ADMIN_PIN)
        for round_key, round_name, status, lock_datetime in DEFAULT_ROUNDS:
            upsert_round(tournament_id, round_key, round_name, status, lock_datetime)

        team_ids: dict[tuple[str, int], int] = {}
        for group_letter, teams in GROUPS.items():
            for idx, team_name in enumerate(teams):
                team_ids[(group_letter, idx)] = add_team(tournament_id, team_name, group_letter)

        for group_letter, _teams in GROUPS.items():
            for matchday, home_idx, away_idx in GENERIC_GROUP_PAIRINGS:
                add_match(
                    tournament_id=tournament_id,
                    round_key="grupos",
                    phase="grupos",
                    group_letter=group_letter,
                    matchday=matchday,
                    home_team_id=team_ids[(group_letter, home_idx)],
                    away_team_id=team_ids[(group_letter, away_idx)],
                    kickoff_datetime=None,
                    origin="seed_demo",
                )
        return tournament_id

    if sheets_enabled():
        with defer_sheets_sync():
            return _seed()
    return _seed()
