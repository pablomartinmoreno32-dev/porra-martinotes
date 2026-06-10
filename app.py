from __future__ import annotations

from datetime import datetime, time
from io import BytesIO

import pandas as pd
import streamlit as st

import database as db
from bracket_fifa import build_round32_pairings, next_round_sources
from scoring import KNOCKOUT_ROUNDS, build_leaderboard, completed_predictions_count, compute_group_standings, compute_third_place_ranking
from seed_data import DEFAULT_TOURNAMENT_CODE, EXTRA_FIELDS, ROUND_KEYS, ROUND_NAMES

st.set_page_config(page_title="Porra Martinotes", page_icon="⚽", layout="wide")


def rerun():
    st.rerun()


def safe_int(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return int(value)
    except Exception:
        return default


def parse_dt(value):
    if not value or pd.isna(value):
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            pass
    return None


def to_dt_string(d, t):
    if not d or not t:
        return None
    return datetime.combine(d, t).strftime("%Y-%m-%d %H:%M:%S")


def is_round_locked(tournament_id: int, round_key: str) -> bool:
    row = db.get_one("SELECT status, lock_datetime FROM rounds WHERE tournament_id=? AND round_key=?", [tournament_id, round_key])
    if not row:
        return False
    if str(row.get("status")) == "closed":
        return True
    dt = parse_dt(row.get("lock_datetime"))
    return bool(dt and datetime.now() >= dt)


def load_matches(tournament_id: int) -> pd.DataFrame:
    return db.query_df(
        """
        SELECT m.*, ht.name AS home_team, at.name AS away_team, wt.name AS winner_team
        FROM matches m
        JOIN teams ht ON ht.id=m.home_team_id
        JOIN teams at ON at.id=m.away_team_id
        LEFT JOIN teams wt ON wt.id=m.winner_team_id
        WHERE m.tournament_id=?
        ORDER BY CASE m.round_key WHEN 'grupos' THEN 1 WHEN 'ronda32' THEN 2 WHEN 'octavos' THEN 3 WHEN 'cuartos' THEN 4 WHEN 'semifinales' THEN 5 WHEN 'final' THEN 6 ELSE 99 END,
                 m.group_letter, m.matchday, CAST(REPLACE(m.bracket_slot,'M','') AS INTEGER), m.id
        """,
        [tournament_id],
    )


def load_teams(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM teams WHERE tournament_id=? ORDER BY group_letter, name", [tournament_id])


def load_participants(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT id, name, created_at FROM participants WHERE tournament_id=? ORDER BY name", [tournament_id])


def load_predictions(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM predictions WHERE tournament_id=?", [tournament_id])


def load_overrides(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM ranking_overrides WHERE tournament_id=?", [tournament_id])


def team_options(teams: pd.DataFrame) -> dict[int, str]:
    return {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}


def login_view():
    st.title("⚽ Porra Martinotes")
    st.caption("Acceso por código de torneo, nombre y PIN. El administrador entra con PIN admin.")
    code = st.text_input("Código del torneo", value=DEFAULT_TOURNAMENT_CODE).strip()
    mode = st.radio("Modo", ["Jugador", "Administrador"], horizontal=True)
    if mode == "Administrador":
        pin = st.text_input("PIN admin", type="password")
        if st.button("Entrar como admin", type="primary"):
            t = db.get_tournament_by_code(code)
            if not t:
                st.error("Código incorrecto.")
            elif t["admin_pin"] != pin:
                st.error("PIN admin incorrecto.")
            else:
                st.session_state.role = "admin"
                st.session_state.tournament_id = int(t["id"])
                st.session_state.join_code = code.upper()
                rerun()
    else:
        name = st.text_input("Tu nombre")
        pin = st.text_input("PIN personal", type="password")
        if st.button("Entrar", type="primary"):
            t = db.get_tournament_by_code(code)
            if not t:
                st.error("Código incorrecto.")
            else:
                ok, msg, pid = db.register_or_login_participant(int(t["id"]), name, pin)
                if ok:
                    st.session_state.role = "player"
                    st.session_state.tournament_id = int(t["id"])
                    st.session_state.participant_id = pid
                    st.session_state.participant_name = name.strip()
                    st.session_state.join_code = code.upper()
                    rerun()
                else:
                    st.error(msg)


def header():
    c1, c2, c3 = st.columns([2, 5, 1])
    c1.write(f"**{st.session_state.get('join_code','')}**")
    c2.write(f"Usuario: **{st.session_state.get('participant_name') or 'Administrador'}**")
    if c3.button("Salir"):
        st.session_state.clear()
        rerun()


def render_prediction_match(m, participant_id: int, tournament_id: int, scope: str, disabled: bool, teams: pd.DataFrame, key_prefix: str):
    pred = db.get_one(
        "SELECT * FROM predictions WHERE tournament_id=? AND participant_id=? AND match_id=? AND scope=?",
        [tournament_id, participant_id, int(m["id"]), scope],
    )
    default_h = safe_int(pred["predicted_home_goals"] if pred else None, 0)
    default_a = safe_int(pred["predicted_away_goals"] if pred else None, 0)
    with st.container(border=True):
        c1, c2, c3, c4, c5, c6 = st.columns([3, 1, 0.2, 1, 3, 3])
        c1.markdown(f"**{m['home_team']}**")
        hg = c2.number_input("GL", min_value=0, max_value=30, value=default_h, key=f"{key_prefix}_h_{scope}_{participant_id}_{m['id']}", label_visibility="collapsed", disabled=disabled)
        c3.markdown("### -")
        ag = c4.number_input("GV", min_value=0, max_value=30, value=default_a, key=f"{key_prefix}_a_{scope}_{participant_id}_{m['id']}", label_visibility="collapsed", disabled=disabled)
        c5.markdown(f"**{m['away_team']}**")
        et = pen = False
        winner = None
        if str(m["round_key"]) != "grupos":
            et = c6.checkbox("Prórroga", value=bool(pred["predicted_extra_time"]) if pred else False, key=f"{key_prefix}_et_{scope}_{participant_id}_{m['id']}", disabled=disabled)
            pen = c6.checkbox("Penaltis", value=bool(pred["predicted_penalties"]) if pred else False, key=f"{key_prefix}_pen_{scope}_{participant_id}_{m['id']}", disabled=disabled)
            ids = [int(m["home_team_id"]), int(m["away_team_id"])]
            names = team_options(teams)
            default_w = safe_int(pred["predicted_winner_team_id"] if pred else None, ids[0])
            winner = c6.selectbox("Pasa", ids, index=ids.index(default_w) if default_w in ids else 0, format_func=lambda x: names.get(int(x), str(x)), key=f"{key_prefix}_win_{scope}_{participant_id}_{m['id']}", disabled=disabled)
            if pen and winner is None:
                st.error("Si marcas penaltis, debes elegir quién pasa.")
    return int(m["id"]), hg, ag, winner, et, pen


def render_prediction_block(tournament_id: int, participant_id: int, scope: str, round_key: str, editable: bool, key_prefix: str):
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    if round_key == "grupos":
        block = matches[matches["round_key"] == "grupos"].copy()
        mdays = sorted([int(x) for x in block["matchday"].dropna().unique().tolist()])
        day = st.selectbox("Jornada", mdays if mdays else [1], key=f"{key_prefix}_day_{participant_id}_{scope}")
        block = block[block["matchday"] == day]
    else:
        block = matches[matches["round_key"] == round_key].copy()
    if block.empty:
        st.info("Todavía no hay partidos pintados para esta ronda.")
        return
    disabled = (not editable) or is_round_locked(tournament_id, round_key)
    if editable and disabled:
        st.warning("Ronda cerrada. Puedes ver tus apuestas, pero no editarlas.")
    to_save = []
    group_cols = ["group_letter"] if round_key == "grupos" else ["round_key"]
    for key, sub in block.groupby(group_cols, dropna=False, sort=True):
        if round_key == "grupos":
            st.markdown(f"#### Grupo {key}")
        for _, m in sub.iterrows():
            to_save.append(render_prediction_match(m, participant_id, tournament_id, scope, disabled, teams, key_prefix))
    if editable:
        if st.button(f"Guardar {ROUND_NAMES.get(round_key, round_key)}", type="primary", disabled=disabled, key=f"{key_prefix}_save_{participant_id}_{scope}"):
            if is_round_locked(tournament_id, round_key):
                st.error("No se puede guardar: ronda cerrada.")
            else:
                with db.defer_sheets_sync():
                    for mid, hg, ag, winner, et, pen in to_save:
                        db.upsert_prediction(tournament_id, participant_id, mid, hg, ag, scope, round_key, winner, et, pen, scope == "initial")
                st.success("Predicciones guardadas.")
                rerun()


def render_predictions(tournament_id: int, participant_id: int):
    st.subheader("Predicciones")
    participants = load_participants(tournament_id)
    others = participants[participants["id"] != participant_id]
    main_tabs = st.tabs(["Inicial", "Ronda de 32", "Octavos", "Cuartos", "Semifinales", "Final"])

    with main_tabs[0]:
        group_matches = load_matches(tournament_id)
        group_matches = group_matches[group_matches["round_key"] == "grupos"]
        preds = load_predictions(tournament_id)
        complete, total = completed_predictions_count(preds[(preds["participant_id"] == participant_id) & (preds["scope"] == "initial")], group_matches)
        st.progress(0 if total == 0 else complete / total, text=f"Mis predicciones: {complete}/{total} partidos")
        labels = ["Mis predicciones"] + [str(r["name"]) for _, r in others.iterrows()]
        tabs = st.tabs(labels)
        with tabs[0]:
            render_prediction_block(tournament_id, participant_id, "initial", "grupos", True, "my_initial")
        for tab, (_, r) in zip(tabs[1:], others.iterrows()):
            with tab:
                st.caption("Solo lectura")
                render_prediction_block(tournament_id, int(r["id"]), "initial", "grupos", False, f"view_initial_{int(r['id'])}")

    for parent_tab, rk in zip(main_tabs[1:], KNOCKOUT_ROUNDS):
        with parent_tab:
            labels = ["Mis predicciones"] + [str(r["name"]) for _, r in others.iterrows()]
            tabs = st.tabs(labels)
            with tabs[0]:
                render_prediction_block(tournament_id, participant_id, rk, rk, True, f"my_{rk}")
            for tab, (_, r) in zip(tabs[1:], others.iterrows()):
                with tab:
                    st.caption("Solo lectura")
                    render_prediction_block(tournament_id, int(r["id"]), rk, rk, False, f"view_{rk}_{int(r['id'])}")


def render_extras(tournament_id: int, participant_id: int, admin: bool = False):
    st.subheader("Extras")
    if not admin:
        rows = db.query_df("SELECT * FROM extra_predictions WHERE tournament_id=? AND participant_id=?", [tournament_id, participant_id])
        current = {r["field_key"]: r["prediction_text"] for _, r in rows.iterrows()}
        with st.form("extras_form"):
            values = {k: st.text_input(label, value=str(current.get(k, "") or "")) for k, label in EXTRA_FIELDS.items()}
            if st.form_submit_button("Guardar extras", type="primary"):
                with db.defer_sheets_sync():
                    for k, v in values.items():
                        db.upsert_extra_prediction(tournament_id, participant_id, k, v)
                st.success("Extras guardados.")
                rerun()
    else:
        participants = load_participants(tournament_id)
        preds = db.query_df("SELECT ep.*, p.name FROM extra_predictions ep JOIN participants p ON p.id=ep.participant_id WHERE ep.tournament_id=? ORDER BY p.name, ep.field_key", [tournament_id])
        vals = db.query_df("SELECT * FROM extra_validations WHERE tournament_id=?", [tournament_id])
        for _, p in participants.iterrows():
            with st.expander(str(p["name"])):
                sub = preds[preds["participant_id"] == int(p["id"])] if not preds.empty else pd.DataFrame()
                for k, label in EXTRA_FIELDS.items():
                    pred = ""
                    if not sub.empty:
                        hit = sub[sub["field_key"] == k]
                        if not hit.empty:
                            pred = str(hit.iloc[0]["prediction_text"] or "")
                    vsub = vals[(vals["participant_id"] == int(p["id"])) & (vals["field_key"] == k)] if not vals.empty else pd.DataFrame()
                    checked = bool(int(vsub.iloc[0]["is_correct"])) if not vsub.empty else False
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"**{label}:** {pred or '—'}")
                    ok = c2.checkbox("Correcto", value=checked, key=f"extra_val_{p['id']}_{k}")
                    if ok != checked:
                        db.validate_extra(tournament_id, int(p["id"]), k, ok)


def render_classifications(tournament_id: int):
    st.subheader("Clasificación fase de grupos")
    matches, teams, overrides = load_matches(tournament_id), load_teams(tournament_id), load_overrides(tournament_id)
    standings = compute_group_standings(matches, teams, overrides)
    if standings.empty:
        st.info("No hay equipos.")
        return
    for group, g in standings.groupby("Grupo"):
        with st.expander(f"Grupo {group}", expanded=True):
            st.dataframe(g[["Pos", "Equipo", "Pts", "PJ", "PG", "PE", "PP", "GF", "GC", "DG"]], hide_index=True, use_container_width=True)
    st.markdown("### Mejores terceros")
    thirds = compute_third_place_ranking(standings, overrides)
    st.dataframe(thirds[["Rank3", "Equipo", "Grupo", "Pts", "DG", "GF", "Clasifica"]], hide_index=True, use_container_width=True)


def render_matches_results(tournament_id: int, admin: bool):
    st.subheader("Partidos y resultados")
    matches, teams = load_matches(tournament_id), load_teams(tournament_id)
    if admin:
        render_match_management(tournament_id, matches, teams)
        st.divider()
    if matches.empty:
        st.info("No hay partidos.")
        return
    c1, c2, c3 = st.columns(3)
    rk = c1.selectbox("Ronda", ["Todos"] + ROUND_KEYS, format_func=lambda x: "Todos" if x == "Todos" else ROUND_NAMES.get(x, x))
    group = c2.selectbox("Grupo", ["Todos"] + sorted([x for x in matches["group_letter"].dropna().unique().tolist()]))
    status = c3.selectbox("Estado", ["Todos", "pending", "played"])
    df = matches.copy()
    if rk != "Todos": df = df[df["round_key"] == rk]
    if group != "Todos": df = df[df["group_letter"] == group]
    if status != "Todos": df = df[df["status"] == status]
    names = team_options(teams)
    changes = []
    for _, m in df.iterrows():
        with st.container(border=True):
            c1, c2, c3, c4, c5, c6 = st.columns([3, 1, .2, 1, 3, 3])
            c1.write(f"**{m['home_team']}**")
            c5.write(f"**{m['away_team']}**")
            if admin:
                hg = c2.number_input("GL", 0, 30, safe_int(m["home_goals"], 0), key=f"real_h_{m['id']}", label_visibility="collapsed")
                c3.markdown("### -")
                ag = c4.number_input("GV", 0, 30, safe_int(m["away_goals"], 0), key=f"real_a_{m['id']}", label_visibility="collapsed")
                winner = None; et = pen = False
                if m["round_key"] != "grupos":
                    et = c6.checkbox("Prórroga", bool(m["extra_time"]), key=f"real_et_{m['id']}")
                    pen = c6.checkbox("Penaltis", bool(m["penalties"]), key=f"real_pen_{m['id']}")
                    ids = [int(m["home_team_id"]), int(m["away_team_id"])]
                    default_w = safe_int(m["winner_team_id"], ids[0])
                    winner = c6.selectbox("Pasa", ids, index=ids.index(default_w) if default_w in ids else 0, format_func=lambda x: names.get(x, x), key=f"real_w_{m['id']}")
                changes.append((int(m["id"]), hg, ag, winner, et, pen))
            else:
                res = "—" if pd.isna(m["home_goals"]) or pd.isna(m["away_goals"]) else f"{int(m['home_goals'])} - {int(m['away_goals'])}"
                c2.markdown(f"### {res}")
                if pd.notna(m.get("winner_team")):
                    c6.caption(f"Pasa: {m['winner_team']}")
    if admin and st.button("Guardar todos los resultados visibles", type="primary"):
        with db.defer_sheets_sync():
            for row in changes:
                db.update_match_result(*row)
        st.success("Resultados guardados.")
        rerun()


def render_match_management(tournament_id: int, matches: pd.DataFrame, teams: pd.DataFrame):
    with st.expander("Crear / editar partidos", expanded=False):
        names = team_options(teams)
        with st.form("create_match"):
            c1, c2, c3 = st.columns(3)
            rk = c1.selectbox("Ronda", ROUND_KEYS, format_func=lambda x: ROUND_NAMES.get(x, x))
            group = c2.text_input("Grupo", value="A" if rk == "grupos" else "")
            md = c3.number_input("Jornada", 0, 20, 1 if rk == "grupos" else 0)
            c4, c5 = st.columns(2)
            home = c4.selectbox("Local", list(names), format_func=lambda x: names[x])
            away = c5.selectbox("Visitante", list(names), format_func=lambda x: names[x])
            if st.form_submit_button("Crear partido"):
                if home == away:
                    st.error("Local y visitante no pueden coincidir.")
                else:
                    db.add_match(tournament_id, rk, rk, group.strip().upper() or None, int(md) or None, home, away, None, None, "manual")
                    st.success("Partido creado.")
                    rerun()
        if not matches.empty:
            labels = {int(r["id"]): f"#{int(r['id'])} {ROUND_NAMES.get(r['round_key'], r['round_key'])} · {r['home_team']} vs {r['away_team']}" for _, r in matches.iterrows()}
            mid = st.selectbox("Eliminar partido", list(labels), format_func=lambda x: labels[x])
            if st.button("Eliminar seleccionado"):
                db.delete_match(mid, tournament_id)
                st.success("Partido eliminado.")
                rerun()


def render_rounds_admin(tournament_id: int):
    st.subheader("Rondas y cierres")
    rounds = db.query_df("SELECT * FROM rounds WHERE tournament_id=? ORDER BY id", [tournament_id])
    for _, r in rounds.iterrows():
        with st.expander(f"{r['round_name']} · {r['round_key']}"):
            dt = parse_dt(r["lock_datetime"])
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            name = c1.text_input("Nombre", value=str(r["round_name"]), key=f"rn_{r['id']}")
            status = c2.selectbox("Estado", ["pending", "open", "closed"], index=["pending", "open", "closed"].index(str(r["status"])), key=f"rs_{r['id']}")
            d = c3.date_input("Fecha cierre", value=dt.date() if dt else None, key=f"rd_{r['id']}")
            t = c4.time_input("Hora", value=dt.time() if dt else time(20, 0), key=f"rt_{r['id']}")
            if st.button("Guardar", key=f"save_round_{r['id']}"):
                db.upsert_round(tournament_id, str(r["round_key"]), name, status, to_dt_string(d, t) if d else None)
                st.success("Ronda guardada.")
                rerun()


def render_score_rules(tournament_id: int):
    st.subheader("Reglas de puntuación")
    rules = db.get_rules(tournament_id)
    with st.form("rules"):
        values = {}
        for k, v in rules.items():
            values[k] = st.number_input(k, value=float(v), step=1.0)
        if st.form_submit_button("Guardar reglas", type="primary"):
            with db.defer_sheets_sync():
                for k, v in values.items():
                    db.set_rule(tournament_id, k, v, sync=False)
            st.success("Reglas guardadas.")
            rerun()


def render_generate_bracket(tournament_id: int):
    st.subheader("Generar eliminatorias")
    matches, teams, overrides = load_matches(tournament_id), load_teams(tournament_id), load_overrides(tournament_id)
    standings = compute_group_standings(matches, teams, overrides)
    thirds = compute_third_place_ranking(standings, overrides)
    if st.button("Generar ronda de 32 FIFA", type="primary"):
        try:
            pairings, official, mapping = build_round32_pairings(standings, thirds)
            with db.defer_sheets_sync():
                for p in pairings:
                    db.add_match(tournament_id, "ronda32", "ronda32", None, None, p["home_team_id"], p["away_team_id"], None, f"M{p['match_no']}", "fifa_annex_c" if official else "fallback")
            if official:
                st.success("Ronda de 32 generada con mapping oficial FIFA/Annex C.")
            else:
                st.warning("Ronda generada con fallback offline. Revisa la conexión para cargar Annex C oficial.")
            st.json(mapping)
            rerun()
        except Exception as exc:
            st.error(f"No se pudo generar la ronda de 32: {exc}")
    for rk in ["octavos", "cuartos", "semifinales", "final"]:
        if st.button(f"Generar {ROUND_NAMES[rk]}"):
            sources = next_round_sources(rk)
            saved = 0
            with db.defer_sheets_sync():
                for match_no, a, b in sources:
                    wa = db.get_one("SELECT winner_team_id FROM matches WHERE tournament_id=? AND bracket_slot=? AND status='played'", [tournament_id, f"M{a}"])
                    wb = db.get_one("SELECT winner_team_id FROM matches WHERE tournament_id=? AND bracket_slot=? AND status='played'", [tournament_id, f"M{b}"])
                    if wa and wb and wa["winner_team_id"] and wb["winner_team_id"]:
                        db.add_match(tournament_id, rk, rk, None, None, int(wa["winner_team_id"]), int(wb["winner_team_id"]), None, f"M{match_no}", "generated")
                        saved += 1
            st.success(f"Partidos generados: {saved}")
            rerun()


def render_leaderboard(tournament_id: int):
    st.subheader("Clasificación general")
    participants = load_participants(tournament_id)
    if participants.empty:
        st.info("No hay jugadores.")
        return
    validations = db.query_df("SELECT * FROM extra_validations WHERE tournament_id=?", [tournament_id])
    lb, _ = build_leaderboard(participants, load_matches(tournament_id), load_teams(tournament_id), load_predictions(tournament_id), load_overrides(tournament_id), db.get_rules(tournament_id), validations)
    st.info("Base de referencia: 1.000 puntos + bonus.")
    st.dataframe(lb[["Pos", "Jugador", "Total", "Grupos", "Eliminatorias", "Extras", "Bonus"]], hide_index=True, use_container_width=True)


def render_backup(tournament_id: int):
    st.subheader("Backup")
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for table in db.SYNC_TABLES:
            db.query_df(f"SELECT * FROM {table} WHERE tournament_id=?" if table != "tournaments" else "SELECT * FROM tournaments", [tournament_id] if table != "tournaments" else []).to_excel(writer, sheet_name=table, index=False)
    st.download_button("Descargar backup Excel", output.getvalue(), "backup_porra_martinotes.xlsx")


def admin_view(tournament_id: int):
    tabs = st.tabs(["Resultados", "Clasificación deportiva", "Generar rondas", "Rondas", "Extras", "Reglas", "Ranking", "Backup"])
    with tabs[0]: render_matches_results(tournament_id, True)
    with tabs[1]: render_classifications(tournament_id)
    with tabs[2]: render_generate_bracket(tournament_id)
    with tabs[3]: render_rounds_admin(tournament_id)
    with tabs[4]: render_extras(tournament_id, 0, True)
    with tabs[5]: render_score_rules(tournament_id)
    with tabs[6]: render_leaderboard(tournament_id)
    with tabs[7]: render_backup(tournament_id)


def player_view(tournament_id: int, participant_id: int):
    tabs = st.tabs(["Predicciones", "Extras", "Clasificación general", "Clasificación grupos", "Resultados"])
    with tabs[0]: render_predictions(tournament_id, participant_id)
    with tabs[1]: render_extras(tournament_id, participant_id)
    with tabs[2]: render_leaderboard(tournament_id)
    with tabs[3]: render_classifications(tournament_id)
    with tabs[4]: render_matches_results(tournament_id, False)


def main():
    db.init_db()
    if "sheets_loaded" not in st.session_state:
        db.pull_from_sheets_if_configured()
        st.session_state.sheets_loaded = True
    st.session_state.setdefault("role", None)
    if not st.session_state.role:
        login_view()
        return
    header()
    if st.session_state.role == "admin":
        admin_view(int(st.session_state.tournament_id))
    else:
        player_view(int(st.session_state.tournament_id), int(st.session_state.participant_id))


if __name__ == "__main__":
    main()
