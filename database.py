from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from seed_data import DEFAULT_ADMIN_PIN, DEFAULT_RULES, DEFAULT_TOURNAMENT_CODE, GROUPS, ROUND_KEYS, ROUND_NAMES

DB_PATH = Path("porra_martinotes.sqlite3")
SYNC_TABLES = [
    "tournaments", "rounds", "participants", "teams", "matches", "predictions",
    "initial_bracket_matches", "ranking_overrides", "scoring_rules", "extra_predictions", "extra_validations",
]

_SYNC_DISABLED = False


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def execute(sql: str, params: Iterable[Any] | None = None) -> None:
    with conn() as c:
        c.execute(sql, list(params or []))
        c.commit()
    sync_to_sheets_if_configured()


def executemany(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    with conn() as c:
        c.executemany(sql, rows)
        c.commit()
    sync_to_sheets_if_configured()


def query_df(sql: str, params: Iterable[Any] | None = None) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(sql, c, params=list(params or []))


def get_one(sql: str, params: Iterable[Any] | None = None) -> dict | None:
    with conn() as c:
        row = c.execute(sql, list(params or [])).fetchone()
        return dict(row) if row else None


@contextlib.contextmanager
def defer_sheets_sync():
    global _SYNC_DISABLED
    old = _SYNC_DISABLED
    _SYNC_DISABLED = True
    try:
        yield
    finally:
        _SYNC_DISABLED = old
        sync_to_sheets_if_configured()


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(str(pin).encode("utf-8")).hexdigest()


def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                admin_pin TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_key TEXT NOT NULL,
                round_name TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                lock_datetime TEXT,
                UNIQUE(tournament_id, round_key)
            );
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                pin_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, name)
            );
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                group_letter TEXT NOT NULL,
                manual_tiebreak_order INTEGER DEFAULT 999,
                UNIQUE(tournament_id, name)
            );
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_key TEXT NOT NULL,
                phase TEXT,
                group_letter TEXT,
                matchday INTEGER,
                bracket_slot TEXT,
                home_team_id INTEGER NOT NULL,
                away_team_id INTEGER NOT NULL,
                kickoff_datetime TEXT,
                home_goals INTEGER,
                away_goals INTEGER,
                winner_team_id INTEGER,
                extra_time INTEGER DEFAULT 0,
                penalties INTEGER DEFAULT 0,
                resolution TEXT,
                status TEXT DEFAULT 'pending',
                origin TEXT DEFAULT 'seed',
                manual_tiebreak_order INTEGER DEFAULT 999,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, round_key, bracket_slot)
            );
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                match_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                round_key TEXT NOT NULL,
                predicted_home_goals INTEGER,
                predicted_away_goals INTEGER,
                predicted_winner_team_id INTEGER,
                predicted_extra_time INTEGER DEFAULT 0,
                predicted_penalties INTEGER DEFAULT 0,
                is_initial INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, match_id, scope)
            );
            CREATE TABLE IF NOT EXISTS initial_bracket_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                round_key TEXT NOT NULL,
                bracket_slot TEXT NOT NULL,
                home_team_id INTEGER NOT NULL,
                away_team_id INTEGER NOT NULL,
                origin TEXT DEFAULT 'player_initial',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, round_key, bracket_slot)
            );
            CREATE TABLE IF NOT EXISTS ranking_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                group_letter TEXT,
                team_id INTEGER NOT NULL,
                manual_order INTEGER DEFAULT 999,
                UNIQUE(tournament_id, scope, group_letter, team_id)
            );
            CREATE TABLE IF NOT EXISTS scoring_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value REAL NOT NULL,
                UNIQUE(tournament_id, rule_key)
            );
            CREATE TABLE IF NOT EXISTS extra_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                field_key TEXT NOT NULL,
                prediction_text TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, field_key)
            );
            CREATE TABLE IF NOT EXISTS extra_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                field_key TEXT NOT NULL,
                is_correct INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, participant_id, field_key)
            );
            """
        )
        c.commit()
    seed_defaults()


def seed_defaults() -> None:
    with defer_sheets_sync():
        t = get_tournament_by_code(DEFAULT_TOURNAMENT_CODE)
        if not t:
            execute("INSERT INTO tournaments(code, name, admin_pin) VALUES(?,?,?)", [DEFAULT_TOURNAMENT_CODE, "Porra Martinotes", DEFAULT_ADMIN_PIN])
            t = get_tournament_by_code(DEFAULT_TOURNAMENT_CODE)
        tid = int(t["id"])
        for rk in ROUND_KEYS:
            upsert_round(tid, rk, ROUND_NAMES[rk], "open" if rk == "grupos" else "pending", None)
        for group, names in GROUPS.items():
            for name in names:
                add_team(tid, name, group)
        ensure_default_rules(tid)
        seed_group_matches(tid)


def get_tournament_by_code(code: str) -> dict | None:
    return get_one("SELECT * FROM tournaments WHERE UPPER(code)=UPPER(?)", [code.strip()])


def register_or_login_participant(tournament_id: int, name: str, pin: str) -> tuple[bool, str, int | None]:
    name = (name or "").strip()
    if not name or not pin:
        return False, "Introduce nombre y PIN.", None
    existing = get_one("SELECT * FROM participants WHERE tournament_id=? AND UPPER(name)=UPPER(?)", [tournament_id, name])
    h = _hash_pin(pin)
    if existing:
        if existing["pin_hash"] != h:
            return False, "PIN incorrecto para ese jugador.", None
        return True, "OK", int(existing["id"])
    execute("INSERT INTO participants(tournament_id, name, pin_hash) VALUES(?,?,?)", [tournament_id, name, h])
    row = get_one("SELECT * FROM participants WHERE tournament_id=? AND UPPER(name)=UPPER(?)", [tournament_id, name])
    return True, "Registrado", int(row["id"])


def upsert_round(tournament_id: int, round_key: str, round_name: str, status: str, lock_datetime: str | None) -> None:
    execute(
        """INSERT INTO rounds(tournament_id, round_key, round_name, status, lock_datetime)
        VALUES(?,?,?,?,?)
        ON CONFLICT(tournament_id, round_key) DO UPDATE SET round_name=excluded.round_name, status=excluded.status, lock_datetime=excluded.lock_datetime""",
        [tournament_id, round_key, round_name, status, lock_datetime],
    )


def add_team(tournament_id: int, name: str, group_letter: str) -> None:
    if not name:
        return
    execute(
        "INSERT OR IGNORE INTO teams(tournament_id, name, group_letter) VALUES(?,?,?)",
        [tournament_id, name.strip(), group_letter.strip().upper()],
    )


def update_team(team_id: int, name: str, group_letter: str) -> None:
    execute("UPDATE teams SET name=?, group_letter=? WHERE id=?", [name.strip(), group_letter.strip().upper(), team_id])


def team_id(tournament_id: int, name: str) -> int:
    row = get_one("SELECT id FROM teams WHERE tournament_id=? AND name=?", [tournament_id, name])
    if not row:
        raise KeyError(name)
    return int(row["id"])


def seed_group_matches(tournament_id: int) -> None:
    count = get_one("SELECT COUNT(*) AS n FROM matches WHERE tournament_id=? AND round_key='grupos'", [tournament_id])
    if count and int(count["n"]) > 0:
        return
    with defer_sheets_sync():
        for group, names in GROUPS.items():
            pairings = [(0, 1, 1), (2, 3, 1), (0, 2, 2), (1, 3, 2), (0, 3, 3), (1, 2, 3)]
            for idx, (a, b, md) in enumerate(pairings, start=1):
                add_match(tournament_id, "grupos", "grupos", group, md, team_id(tournament_id, names[a]), team_id(tournament_id, names[b]), None, f"G{group}-J{md}-{idx}", "seed")


def add_match(tournament_id: int, round_key: str, phase: str, group_letter: str | None, matchday: int | None, home_team_id: int, away_team_id: int, kickoff: str | None, bracket_slot: str | None = None, origin: str = "manual") -> None:
    slot = bracket_slot or f"{round_key}-{home_team_id}-{away_team_id}"
    execute(
        """INSERT OR REPLACE INTO matches(tournament_id, round_key, phase, group_letter, matchday, bracket_slot, home_team_id, away_team_id, kickoff_datetime, origin)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [tournament_id, round_key, phase, group_letter, matchday, slot, home_team_id, away_team_id, kickoff, origin],
    )


def delete_match(match_id: int, tournament_id: int) -> None:
    execute("DELETE FROM predictions WHERE match_id=? AND tournament_id=?", [match_id, tournament_id])
    execute("DELETE FROM matches WHERE id=? AND tournament_id=?", [match_id, tournament_id])


def update_match_result(
    match_id: int,
    home_goals: int | None,
    away_goals: int | None,
    winner_team_id: int | None,
    extra_time: bool,
    penalties: bool,
    status: str = "played",
) -> None:
    status = str(status or "pending").strip().lower()
    if status not in ["pending", "played"]:
        status = "pending"

    row = get_one("SELECT home_team_id, away_team_id, round_key FROM matches WHERE id=?", [match_id])

    if status == "pending":
        winner_team_id = None
        extra_time = False
        penalties = False
    else:
        if home_goals is None or away_goals is None:
            status = "pending"
            winner_team_id = None
            extra_time = False
            penalties = False
        elif row and row["round_key"] == "grupos":
            if home_goals > away_goals:
                winner_team_id = int(row["home_team_id"])
            elif away_goals > home_goals:
                winner_team_id = int(row["away_team_id"])
            else:
                winner_team_id = None

    execute(
        """
        UPDATE matches
        SET home_goals=?,
            away_goals=?,
            winner_team_id=?,
            extra_time=?,
            penalties=?,
            status=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        [home_goals, away_goals, winner_team_id, int(bool(extra_time)), int(bool(penalties)), status, match_id],
    )

def upsert_prediction(tournament_id: int, participant_id: int, match_id: int, hg: int | None, ag: int | None, scope: str, round_key: str, winner: int | None, et: bool, pen: bool, is_initial: bool) -> None:
    execute(
        """INSERT INTO predictions(tournament_id, participant_id, match_id, scope, round_key, predicted_home_goals, predicted_away_goals, predicted_winner_team_id, predicted_extra_time, predicted_penalties, is_initial)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tournament_id, participant_id, match_id, scope) DO UPDATE SET
        predicted_home_goals=excluded.predicted_home_goals,
        predicted_away_goals=excluded.predicted_away_goals,
        predicted_winner_team_id=excluded.predicted_winner_team_id,
        predicted_extra_time=excluded.predicted_extra_time,
        predicted_penalties=excluded.predicted_penalties,
        is_initial=excluded.is_initial,
        updated_at=CURRENT_TIMESTAMP""",
        [tournament_id, participant_id, match_id, scope, round_key, hg, ag, winner, int(bool(et)), int(bool(pen)), int(bool(is_initial))],
    )


def ensure_default_rules(tournament_id: int) -> None:
    with defer_sheets_sync():
        for k, v in DEFAULT_RULES.items():
            set_rule(tournament_id, k, v, sync=False)


def get_rules(tournament_id: int) -> dict[str, float]:
    rows = query_df("SELECT rule_key, rule_value FROM scoring_rules WHERE tournament_id=?", [tournament_id])
    rules = dict(DEFAULT_RULES)
    for _, r in rows.iterrows():
        rules[str(r["rule_key"])] = float(r["rule_value"])
    return rules


def set_rule(tournament_id: int, key: str, value: float, sync: bool = True) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO scoring_rules(tournament_id, rule_key, rule_value) VALUES(?,?,?)
            ON CONFLICT(tournament_id, rule_key) DO UPDATE SET rule_value=excluded.rule_value""",
            [tournament_id, key, float(value)],
        )
        c.commit()
    if sync:
        sync_to_sheets_if_configured()


def upsert_extra_prediction(tournament_id: int, participant_id: int, field_key: str, text: str) -> None:
    execute(
        """INSERT INTO extra_predictions(tournament_id, participant_id, field_key, prediction_text)
        VALUES(?,?,?,?) ON CONFLICT(tournament_id, participant_id, field_key) DO UPDATE SET prediction_text=excluded.prediction_text, updated_at=CURRENT_TIMESTAMP""",
        [tournament_id, participant_id, field_key, text],
    )


def validate_extra(tournament_id: int, participant_id: int, field_key: str, correct: bool) -> None:
    execute(
        """INSERT INTO extra_validations(tournament_id, participant_id, field_key, is_correct)
        VALUES(?,?,?,?) ON CONFLICT(tournament_id, participant_id, field_key) DO UPDATE SET is_correct=excluded.is_correct, updated_at=CURRENT_TIMESTAMP""",
        [tournament_id, participant_id, field_key, int(bool(correct))],
    )



def clear_initial_bracket_round(tournament_id: int, participant_id: int, round_key: str) -> None:
    rows = query_df(
        "SELECT id FROM initial_bracket_matches WHERE tournament_id=? AND participant_id=? AND round_key=?",
        [tournament_id, participant_id, round_key],
    )
    with conn() as c:
        for _, r in rows.iterrows():
            c.execute(
                "DELETE FROM predictions WHERE tournament_id=? AND participant_id=? AND scope='initial' AND match_id=?",
                [tournament_id, participant_id, -int(r["id"])],
            )
        c.execute(
            "DELETE FROM initial_bracket_matches WHERE tournament_id=? AND participant_id=? AND round_key=?",
            [tournament_id, participant_id, round_key],
        )
        c.commit()
    sync_to_sheets_if_configured()


def clear_initial_bracket_rounds(tournament_id: int, participant_id: int, round_keys: list[str]) -> None:
    for rk in round_keys:
        clear_initial_bracket_round(tournament_id, participant_id, rk)


def add_initial_bracket_match(tournament_id: int, participant_id: int, round_key: str, bracket_slot: str, home_team_id: int, away_team_id: int, origin: str = "player_initial") -> None:
    execute(
        """INSERT INTO initial_bracket_matches(tournament_id, participant_id, round_key, bracket_slot, home_team_id, away_team_id, origin)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(tournament_id, participant_id, round_key, bracket_slot) DO UPDATE SET
        home_team_id=excluded.home_team_id,
        away_team_id=excluded.away_team_id,
        origin=excluded.origin,
        updated_at=CURRENT_TIMESTAMP""",
        [tournament_id, participant_id, round_key, bracket_slot, home_team_id, away_team_id, origin],
    )


def load_initial_bracket_matches(tournament_id: int, participant_id: int, round_key: str) -> pd.DataFrame:
    return query_df(
        """
        SELECT -ibm.id AS id, ibm.id AS initial_match_id, ibm.tournament_id, ibm.participant_id,
               ibm.round_key, ibm.bracket_slot, NULL AS phase, NULL AS group_letter, NULL AS matchday,
               ibm.home_team_id, ibm.away_team_id,
               ht.name AS home_team, at.name AS away_team, NULL AS winner_team,
               NULL AS kickoff_datetime, NULL AS home_goals, NULL AS away_goals, NULL AS winner_team_id,
               0 AS extra_time, 0 AS penalties, NULL AS resolution, 'initial_projection' AS status, ibm.origin,
               999 AS manual_tiebreak_order, ibm.created_at, ibm.updated_at
        FROM initial_bracket_matches ibm
        JOIN teams ht ON ht.id=ibm.home_team_id
        JOIN teams at ON at.id=ibm.away_team_id
        WHERE ibm.tournament_id=? AND ibm.participant_id=? AND ibm.round_key=?
        ORDER BY CAST(REPLACE(ibm.bracket_slot,'M','') AS INTEGER), ibm.id
        """,
        [tournament_id, participant_id, round_key],
    )


def sheets_configured() -> bool:
    try:
        return bool(st.secrets.get("GOOGLE_SHEET_ID") or st.secrets.get("google_sheet_id")) and bool(st.secrets.get("gcp_service_account"))
    except Exception:
        return False


def _gspread_client():
    import gspread
    creds = dict(st.secrets["gcp_service_account"])
    return gspread.service_account_from_dict(creds)


def pull_from_sheets_if_configured() -> None:
    if not sheets_configured():
        return
    try:
        gc = _gspread_client()
        sid = st.secrets.get("GOOGLE_SHEET_ID") or st.secrets.get("google_sheet_id")
        sh = gc.open_by_key(sid)
        with defer_sheets_sync():
            for table in SYNC_TABLES:
                try:
                    ws = sh.worksheet(table)
                    records = ws.get_all_records()
                    if not records:
                        continue
                    df = pd.DataFrame(records)
                    with conn() as c:
                        c.execute(f"DELETE FROM {table}")
                        df.to_sql(table, c, if_exists="append", index=False)
                        c.commit()
                except Exception:
                    continue
    except Exception as exc:
        st.warning(f"No se pudo leer Google Sheets. Se usará SQLite local. Detalle: {exc}")


def sync_to_sheets_if_configured() -> None:
    if _SYNC_DISABLED or not sheets_configured():
        return
    try:
        gc = _gspread_client()
        sid = st.secrets.get("GOOGLE_SHEET_ID") or st.secrets.get("google_sheet_id")
        sh = gc.open_by_key(sid)
        for table in SYNC_TABLES:
            df = query_df(f"SELECT * FROM {table}")
            try:
                ws = sh.worksheet(table)
            except Exception:
                ws = sh.add_worksheet(title=table, rows=max(100, len(df) + 10), cols=max(20, len(df.columns) + 2))
            ws.clear()
            values = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
            if values:
                ws.update(values)
    except Exception:
        # Do not break the app for sync issues.
        pass
