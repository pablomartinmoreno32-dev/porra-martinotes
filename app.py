from __future__ import annotations

from datetime import datetime, time
from io import BytesIO

import pandas as pd
import streamlit as st

import database as db
from scoring import (
    KNOCKOUT_ROUNDS,
    build_leaderboard,
    completed_predictions_count,
    compute_group_standings,
    compute_third_place_ranking,
)

st.set_page_config(page_title="Porra Martinotes", page_icon="🏆", layout="wide")

ROUND_LABELS = {
    "grupos": "Fase de grupos",
    "ronda32": "Ronda de 32",
    "octavos": "Octavos",
    "cuartos": "Cuartos",
    "semifinales": "Semifinales",
    "final": "Final",
}

ROUND_KEYS = ["grupos", "ronda32", "octavos", "cuartos", "semifinales", "final"]


def rerun() -> None:
    st.rerun()


def safe_int(value, default: int | None = None) -> int | None:
    try:
        if value is None or pd.isna(value):
            return default
        return int(value)
    except Exception:
        return default


def to_dt_string(d, t) -> str | None:
    if d is None or t is None:
        return None
    return datetime.combine(d, t).strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value: str | None):
    if not value or pd.isna(value):
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            pass
    return None


def is_round_locked(tournament_id: int, round_key: str) -> bool:
    row = db.get_one("SELECT lock_datetime FROM rounds WHERE tournament_id=? AND round_key=?", [tournament_id, round_key])
    dt = parse_dt(row["lock_datetime"] if row else None)
    return bool(dt and datetime.now() >= dt)


def get_round_name(round_key: str) -> str:
    return ROUND_LABELS.get(round_key, round_key)


def ensure_session() -> None:
    defaults = {
        "role": None,
        "tournament_id": None,
        "participant_id": None,
        "participant_name": None,
        "join_code": None,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def load_teams(tournament_id: int) -> pd.DataFrame:
    return db.query_df(
        "SELECT id, name, group_letter, manual_tiebreak_order FROM teams WHERE tournament_id=? ORDER BY group_letter, name",
        [tournament_id],
    )


def load_matches(tournament_id: int) -> pd.DataFrame:
    return db.query_df(
        """
        SELECT
            m.id, m.tournament_id, m.round_key, m.phase, m.group_letter, m.matchday, m.bracket_slot,
            m.home_team_id, m.away_team_id, ht.name AS home_team, at.name AS away_team,
            m.kickoff_datetime, m.home_goals, m.away_goals, m.winner_team_id,
            wt.name AS winner_team, m.extra_time, m.penalties, m.resolution, m.status, m.origin,
            m.manual_tiebreak_order
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN teams wt ON wt.id = m.winner_team_id
        WHERE m.tournament_id=?
        ORDER BY
            CASE m.round_key
                WHEN 'grupos' THEN 1 WHEN 'ronda32' THEN 2 WHEN 'octavos' THEN 3
                WHEN 'cuartos' THEN 4 WHEN 'semifinales' THEN 5 WHEN 'final' THEN 6 ELSE 99 END,
            m.group_letter, m.matchday, m.id
        """,
        [tournament_id],
    )


def load_predictions(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM predictions WHERE tournament_id=?", [tournament_id])


def load_participants(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT id, name, created_at FROM participants WHERE tournament_id=? ORDER BY name", [tournament_id])


def load_overrides(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM ranking_overrides WHERE tournament_id=?", [tournament_id])


def login_view() -> None:
    st.title("🏆 Porra Martinotes")
    st.caption("Acceso por código de torneo, nombre y PIN. El administrador entra con PIN admin.")
    code = st.text_input("Código del torneo", value="MARTINOTES").strip()
    mode = st.radio("Modo de acceso", ["Jugador", "Administrador"], horizontal=True)

    if mode == "Administrador":
        pin = st.text_input("PIN admin", type="password")
        if st.button("Entrar como admin", type="primary"):
            tournament = db.get_tournament_by_code(code)
            if not tournament:
                st.error("Código de torneo incorrecto.")
            elif tournament["admin_pin"] != pin:
                st.error("PIN admin incorrecto.")
            else:
                db.ensure_default_rules(int(tournament["id"]))
                st.session_state.role = "admin"
                st.session_state.tournament_id = int(tournament["id"])
                st.session_state.join_code = code.upper()
                rerun()
    else:
        name = st.text_input("Tu nombre")
        pin = st.text_input("PIN personal", type="password", help="La primera vez lo eliges tú. Después tendrás que usar el mismo.")
        if st.button("Entrar", type="primary"):
            tournament = db.get_tournament_by_code(code)
            if not tournament:
                st.error("Código de torneo incorrecto.")
            else:
                ok, msg, pid = db.register_or_login_participant(int(tournament["id"]), name, pin)
                if ok:
                    st.session_state.role = "player"
                    st.session_state.tournament_id = int(tournament["id"])
                    st.session_state.participant_id = pid
                    st.session_state.participant_name = name.strip()
                    st.session_state.join_code = code.upper()
                    rerun()
                else:
                    st.error(msg)


def logout_button() -> None:
    cols = st.columns([1, 5, 1])
    with cols[0]:
        st.write(f"**{st.session_state.join_code or ''}**")
    with cols[2]:
        if st.button("Salir"):
            for k in ["role", "tournament_id", "participant_id", "participant_name", "join_code"]:
                st.session_state[k] = None
            rerun()


def render_score_rules(tournament_id: int) -> None:
    st.subheader("Reglas de puntuación")
    st.caption("Los porcentajes quedan guardados en la base de datos. La app validará que los bloques principales sumen 100%.")
    rules = db.get_rules(tournament_id)

    with st.form("score_rules_form"):
        st.markdown("#### Pesos globales")
        c1, c2, c3, c4 = st.columns(4)
        base_points = c1.number_input("Puntos base", min_value=1.0, value=float(rules["base_points"]), step=50.0)
        groups = c2.number_input("Fase de grupos (%)", min_value=0.0, max_value=100.0, value=float(rules["global_groups_weight"]), step=1.0)
        knock = c3.number_input("Eliminatorias (%)", min_value=0.0, max_value=100.0, value=float(rules["global_knockout_weight"]), step=1.0)
        extras = c4.number_input("Extras (%)", min_value=0.0, max_value=100.0, value=float(rules["global_extras_weight"]), step=1.0)

        st.markdown("#### Dentro de fase de grupos")
        g1, g2, g3 = st.columns(3)
        group_pos = g1.number_input("Posiciones finales (%)", 0.0, 100.0, float(rules["group_positions_weight"]), 1.0)
        group_sign = g2.number_input("Signo de partidos (%)", 0.0, 100.0, float(rules["group_sign_weight"]), 1.0)
        group_exact = g3.number_input("Marcador exacto (%)", 0.0, 100.0, float(rules["group_exact_weight"]), 1.0)

        st.markdown("#### Dentro de eliminatorias")
        k1, k2, k3, k4 = st.columns(4)
        ko_qual = k1.number_input("Quién pasa ronda (%)", 0.0, 100.0, float(rules["knockout_qualifier_weight"]), 1.0)
        ko_result = k2.number_input("Resultado (%)", 0.0, 100.0, float(rules["knockout_result_weight"]), 1.0)
        et_mult = k3.number_input("Prórroga vs marcador", 0.0, 5.0, float(rules["extra_time_multiplier"]), 0.1)
        pen_mult = k4.number_input("Penaltis vs marcador", 0.0, 5.0, float(rules["penalties_multiplier"]), 0.1)

        st.markdown("#### Bonus por predicción inicial")
        b1, b2, b3, b4, b5 = st.columns(5)
        bonus_oct = b1.number_input("Equipo en octavos", 0.0, 100.0, float(rules.get("bonus_octavos", 1.0)), 1.0)
        bonus_cua = b2.number_input("Equipo en cuartos", 0.0, 100.0, float(rules.get("bonus_cuartos", 3.0)), 1.0)
        bonus_sem = b3.number_input("Equipo en semis", 0.0, 100.0, float(rules.get("bonus_semifinales", 6.0)), 1.0)
        bonus_fin = b4.number_input("Finalista", 0.0, 100.0, float(rules.get("bonus_final", 12.0)), 1.0)
        bonus_cam = b5.number_input("Campeón", 0.0, 100.0, float(rules.get("bonus_campeon", 25.0)), 1.0)

        st.markdown("#### Extras")
        e1, e2, e3, e4, e5, e6 = st.columns(6)
        extra_bo = e1.number_input("Balón de Oro", 0.0, 100.0, float(rules.get("extra_balon_oro", 25.0)), 1.0)
        extra_bg = e2.number_input("Bota de Oro", 0.0, 100.0, float(rules.get("extra_bota_oro", 15.0)), 1.0)
        extra_go = e3.number_input("Guante de Oro", 0.0, 100.0, float(rules.get("extra_guante_oro", 15.0)), 1.0)
        extra_jv = e4.number_input("Mejor joven", 0.0, 100.0, float(rules.get("extra_mejor_joven", 15.0)), 1.0)
        extra_eq = e5.number_input("Equipo entretenido", 0.0, 100.0, float(rules.get("extra_equipo_entretenido", 15.0)), 1.0)
        extra_gt = e6.number_input("Gol del torneo", 0.0, 100.0, float(rules.get("extra_gol_torneo", 15.0)), 1.0)

        total_global = groups + knock + extras
        total_group = group_pos + group_sign + group_exact
        total_ko = ko_qual + ko_result
        st.info(f"Validación: global={total_global:.1f}% · grupos={total_group:.1f}% · eliminatorias={total_ko:.1f}%")
        submitted = st.form_submit_button("Guardar reglas", type="primary")

    if submitted:
        if round(total_global, 4) != 100 or round(total_group, 4) != 100 or round(total_ko, 4) != 100:
            st.error("No guardado: los porcentajes deben sumar 100% en cada bloque.")
        else:
            values = {
                "base_points": base_points,
                "global_groups_weight": groups,
                "global_knockout_weight": knock,
                "global_extras_weight": extras,
                "group_positions_weight": group_pos,
                "group_sign_weight": group_sign,
                "group_exact_weight": group_exact,
                "knockout_qualifier_weight": ko_qual,
                "knockout_result_weight": ko_result,
                "extra_time_multiplier": et_mult,
                "penalties_multiplier": pen_mult,
                "bonus_octavos": bonus_oct,
                "bonus_cuartos": bonus_cua,
                "bonus_semifinales": bonus_sem,
                "bonus_final": bonus_fin,
                "bonus_campeon": bonus_cam,
                "extra_balon_oro": extra_bo,
                "extra_bota_oro": extra_bg,
                "extra_guante_oro": extra_go,
                "extra_mejor_joven": extra_jv,
                "extra_equipo_entretenido": extra_eq,
                "extra_gol_torneo": extra_gt,
            }
            with db.defer_sheets_sync():
                for k, v in values.items():
                    db.set_rule(tournament_id, k, v)
            st.success("Reglas guardadas.")
            rerun()


def render_rounds_admin(tournament_id: int) -> None:
    st.subheader("Rondas y cierres")
    rounds = db.query_df("SELECT * FROM rounds WHERE tournament_id=? ORDER BY id", [tournament_id])
    for _, r in rounds.iterrows():
        with st.expander(f"{r['round_name']} · {r['round_key']}", expanded=False):
            dt = parse_dt(r["lock_datetime"])
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            name = c1.text_input("Nombre", value=str(r["round_name"]), key=f"round_name_{r['id']}")
            status = c2.selectbox("Estado", ["pending", "open", "closed"], index=["pending", "open", "closed"].index(str(r["status"])), key=f"round_status_{r['id']}")
            d = c3.date_input("Fecha cierre", value=dt.date() if dt else None, key=f"round_date_{r['id']}")
            t = c4.time_input("Hora", value=dt.time() if dt else time(20, 0), key=f"round_time_{r['id']}")
            if st.button("Guardar ronda", key=f"save_round_{r['id']}"):
                db.upsert_round(tournament_id, str(r["round_key"]), name, status, to_dt_string(d, t))
                st.success("Ronda guardada.")
                rerun()


def render_match_management(tournament_id: int, matches: pd.DataFrame, teams: pd.DataFrame) -> None:
    st.markdown("### Gestión de partidos")
    st.caption("Usa Crear partido solo para excepciones. El flujo normal es importar calendario y editar si hay errores.")
    team_options = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
    if not team_options:
        st.warning("Antes de crear o editar partidos tiene que haber equipos cargados.")
        return

    tab_create, tab_edit = st.tabs(["Crear partido", "Editar partido"])

    with tab_create:
        with st.form("create_match_form"):
            c1, c2, c3 = st.columns(3)
            rk = c1.selectbox("Ronda", ROUND_KEYS, format_func=get_round_name, key="create_match_round")
            phase = c2.text_input("Fase", value="grupos" if rk == "grupos" else rk, key="create_match_phase")
            grp = c3.text_input("Grupo", value="A" if rk == "grupos" else "", key="create_match_group")
            c4, c5, c6 = st.columns(3)
            md = c4.number_input("Jornada", min_value=0, max_value=20, value=1 if rk == "grupos" else 0, step=1, key="create_match_day")
            home = c5.selectbox("Equipo local", list(team_options.keys()), format_func=lambda x: team_options[int(x)], key="create_match_home")
            away = c6.selectbox("Equipo visitante", list(team_options.keys()), format_func=lambda x: team_options[int(x)], key="create_match_away")
            c7, c8 = st.columns(2)
            d = c7.date_input("Fecha", value=None, key="create_match_date")
            t = c8.time_input("Hora", value=time(20, 0), key="create_match_time")
            submitted = st.form_submit_button("Crear partido", type="primary")
            if submitted:
                if int(home) == int(away):
                    st.error("El equipo local y visitante no pueden ser el mismo.")
                else:
                    kickoff = to_dt_string(d, t) if d else None
                    db.add_match(
                        tournament_id,
                        rk,
                        phase.strip() or rk,
                        grp.strip().upper() or None,
                        int(md) if int(md) > 0 else None,
                        int(home),
                        int(away),
                        kickoff,
                        "manual",
                    )
                    st.success("Partido creado.")
                    st.cache_data.clear()
                    rerun()

    with tab_edit:
        if matches.empty:
            st.info("No hay partidos para editar.")
            return
        match_labels = {}
        for _, m in matches.iterrows():
            md_txt = f"J{int(m['matchday'])}" if pd.notna(m["matchday"]) else "sin jornada"
            grp_txt = f"G{m['group_letter']}" if pd.notna(m["group_letter"]) and str(m["group_letter"]) else "sin grupo"
            label = f"#{int(m['id'])} · {get_round_name(str(m['round_key']))} · {grp_txt} · {md_txt} · {m['home_team']} vs {m['away_team']}"
            match_labels[int(m["id"])] = label
        selected_id = st.selectbox("Partido", list(match_labels.keys()), format_func=lambda x: match_labels[int(x)], key="edit_match_select")
        m = matches[matches["id"] == selected_id].iloc[0]
        with st.form(f"edit_match_form_{selected_id}"):
            c1, c2, c3, c4 = st.columns(4)
            rk = c1.selectbox("Ronda", ROUND_KEYS, index=ROUND_KEYS.index(str(m["round_key"])) if str(m["round_key"]) in ROUND_KEYS else 0, format_func=get_round_name, key=f"edit_match_round_{selected_id}")
            phase = c2.text_input("Fase", value=str(m["phase"]), key=f"edit_match_phase_{selected_id}")
            grp = c3.text_input("Grupo", value="" if pd.isna(m["group_letter"]) else str(m["group_letter"]), key=f"edit_match_group_{selected_id}")
            md_default = safe_int(m["matchday"], 0) or 0
            md = c4.number_input("Jornada", min_value=0, max_value=20, value=md_default, step=1, key=f"edit_match_day_{selected_id}")
            c5, c6, c7 = st.columns(3)
            home_ids = list(team_options.keys())
            home_idx = home_ids.index(int(m["home_team_id"])) if int(m["home_team_id"]) in home_ids else 0
            away_idx = home_ids.index(int(m["away_team_id"])) if int(m["away_team_id"]) in home_ids else 0
            home = c5.selectbox("Equipo local", home_ids, index=home_idx, format_func=lambda x: team_options[int(x)], key=f"edit_match_home_{selected_id}")
            away = c6.selectbox("Equipo visitante", home_ids, index=away_idx, format_func=lambda x: team_options[int(x)], key=f"edit_match_away_{selected_id}")
            status = c7.selectbox("Estado", ["pending", "played"], index=["pending", "played"].index(str(m["status"])) if str(m["status"]) in ["pending", "played"] else 0, key=f"edit_match_status_{selected_id}")
            dt = parse_dt(m["kickoff_datetime"])
            c8, c9 = st.columns(2)
            d = c8.date_input("Fecha", value=dt.date() if dt else None, key=f"edit_match_date_{selected_id}")
            t = c9.time_input("Hora", value=dt.time() if dt else time(20, 0), key=f"edit_match_time_{selected_id}")
            b1, b2 = st.columns(2)
            submitted = b1.form_submit_button("Guardar edición", type="primary")
            delete_clicked = b2.form_submit_button("Eliminar partido")
            if submitted:
                if int(home) == int(away):
                    st.error("El equipo local y visitante no pueden ser el mismo.")
                else:
                    kickoff = to_dt_string(d, t) if d else None
                    db.execute(
                        """
                        UPDATE matches
                        SET round_key=?, phase=?, group_letter=?, matchday=?, home_team_id=?, away_team_id=?,
                            kickoff_datetime=?, status=?, origin='editado', updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND tournament_id=?
                        """,
                        [rk, phase.strip() or rk, grp.strip().upper() or None, int(md) if int(md) > 0 else None, int(home), int(away), kickoff, status, int(selected_id), tournament_id],
                    )
                    st.success("Partido editado.")
                    st.cache_data.clear()
                    rerun()
            if delete_clicked:
                db.delete_match(int(selected_id), tournament_id)
                st.success("Partido eliminado.")
                st.cache_data.clear()
                rerun()


def render_matches_results(tournament_id: int, admin: bool) -> None:
    st.subheader("Partidos y resultados" if admin else "Partidos/resultados")
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    if matches.empty:
        st.warning("No hay partidos cargados.")
        if admin:
            render_match_management(tournament_id, matches, teams)
        return

    if admin:
        render_match_management(tournament_id, matches, teams)
        st.divider()

    c1, c2, c3, c4 = st.columns(4)
    round_options = ["Todos"] + [get_round_name(x) for x in ROUND_KEYS]
    round_sel_label = c1.selectbox("Ronda", round_options, key=f"match_filter_round_{admin}")
    round_sel = "Todos" if round_sel_label == "Todos" else {v: k for k, v in ROUND_LABELS.items()}[round_sel_label]
    groups = ["Todos"] + sorted([x for x in matches["group_letter"].dropna().unique().tolist()])
    group_sel = c2.selectbox("Grupo", groups, key=f"match_filter_group_{admin}")
    jornadas = ["Todas"] + sorted([int(x) for x in matches["matchday"].dropna().unique().tolist()])
    matchday_sel = c3.selectbox("Jornada", jornadas, key=f"match_filter_day_{admin}")
    status_sel = c4.selectbox("Estado", ["Todos", "pending", "played"], key=f"match_filter_status_{admin}")

    df = matches.copy()
    if round_sel != "Todos":
        df = df[df["round_key"] == round_sel]
    if group_sel != "Todos":
        df = df[df["group_letter"] == group_sel]
    if matchday_sel != "Todas":
        df = df[df["matchday"] == matchday_sel]
    if status_sel != "Todos":
        df = df[df["status"] == status_sel]

    st.caption(f"Partidos visibles: {len(df)}")
    if df.empty:
        st.info("No hay partidos con esos filtros.")
        return

    team_options = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
    changes = []
    for (rk, md, grp), block in df.groupby(["round_key", "matchday", "group_letter"], dropna=False, sort=True):
        label = f"{get_round_name(str(rk))}"
        if pd.notna(md):
            label += f" · Jornada {int(md)}"
        if pd.notna(grp):
            label += f" · Grupo {grp}"
        st.markdown(f"### {label}")
        for _, m in block.iterrows():
            mid = int(m["id"])
            with st.container(border=True):
                left, gh, dash, ga, right, extra = st.columns([3, 1, 0.2, 1, 3, 3])
                left.markdown(f"**{m['home_team']}**")
                right.markdown(f"**{m['away_team']}**")
                if admin:
                    hg = gh.number_input("GL", min_value=0, max_value=30, value=safe_int(m["home_goals"], 0), key=f"real_hg_{mid}", label_visibility="collapsed")
                    dash.markdown("### -")
                    ag = ga.number_input("GV", min_value=0, max_value=30, value=safe_int(m["away_goals"], 0), key=f"real_ag_{mid}", label_visibility="collapsed")
                    if str(m["round_key"]) != "grupos":
                        et = extra.checkbox("Prórroga", value=bool(m["extra_time"]), key=f"real_et_{mid}")
                        pen = extra.checkbox("Penaltis", value=bool(m["penalties"]), key=f"real_pen_{mid}")
                        winner_ids = [int(m["home_team_id"]), int(m["away_team_id"])]
                        default_winner = safe_int(m["winner_team_id"], winner_ids[0])
                        winner = extra.selectbox(
                            "Pasa",
                            winner_ids,
                            format_func=lambda x: team_options.get(int(x), str(x)),
                            index=winner_ids.index(default_winner) if default_winner in winner_ids else 0,
                            key=f"real_winner_{mid}",
                        )
                    else:
                        et, pen, winner = False, False, None
                    changes.append((mid, hg, ag, winner, et, pen))
                else:
                    res = "-" if pd.isna(m["home_goals"]) or pd.isna(m["away_goals"]) else f"{int(m['home_goals'])} - {int(m['away_goals'])}"
                    gh.markdown(f"### {res}")
                    if str(m["round_key"]) != "grupos" and pd.notna(m["winner_team"]):
                        extra.caption(f"Pasa: {m['winner_team']}")
                        if bool(m["extra_time"]):
                            extra.caption("Prórroga")
                        if bool(m["penalties"]):
                            extra.caption("Penaltis")
    if admin:
        st.caption("Introduce o modifica varios marcadores y pulsa el botón para persistir todos los resultados visibles.")
        if st.button("Guardar todos los resultados visibles", type="primary", key="save_all_visible_results"):
            saved = 0
            with db.defer_sheets_sync():
                for row in changes:
                    db.update_match_result(*row)
                    saved += 1
            st.cache_data.clear()
            st.success(f"Se han guardado {saved} resultados visibles. La clasificación se recalcula automáticamente.")
            rerun()


def render_classifications(tournament_id: int) -> None:
    st.subheader("Clasificación fase de grupos")
    st.caption("El cuadro/bracket de eliminatorias irá en una pestaña propia cuando implementemos la generación de rondas.")
    if st.button("Actualizar clasificación", key="refresh_group_classification"):
        st.cache_data.clear()
        rerun()
    teams = load_teams(tournament_id)
    matches = load_matches(tournament_id)
    overrides = load_overrides(tournament_id)
    standings = compute_group_standings(matches, teams, overrides)
    if standings.empty:
        st.info("No hay equipos suficientes.")
        return
    for group, df in standings.groupby("Grupo", sort=True):
        with st.expander(f"Grupo {group}", expanded=True):
            compact = df[["Pos", "Equipo", "Pts", "PJ", "DG"]].copy()
            st.dataframe(compact, hide_index=True, use_container_width=True)
            with st.expander("Detalle"):
                st.dataframe(df[["Pos", "Equipo", "Pts", "PJ", "PG", "PE", "PP", "GF", "GC", "DG"]], hide_index=True, use_container_width=True)
    st.markdown("### Ranking de terceros")
    thirds = compute_third_place_ranking(standings, overrides)
    if thirds.empty:
        st.info("Todavía no hay terceros calculables.")
    else:
        st.dataframe(thirds[["Rank3", "Equipo", "Grupo", "Pts", "DG", "GF", "Clasifica"]], hide_index=True, use_container_width=True)


def render_teams_admin(tournament_id: int) -> None:
    st.subheader("Equipos")
    teams = load_teams(tournament_id)
    group_filter = st.selectbox("Filtrar grupo", ["Todos"] + sorted(teams["group_letter"].unique().tolist()) if not teams.empty else ["Todos"], key="teams_group_filter")
    df = teams if group_filter == "Todos" else teams[teams["group_letter"] == group_filter]
    for _, r in df.iterrows():
        c1, c2, c3 = st.columns([3, 1, 1])
        name = c1.text_input("Equipo", value=str(r["name"]), key=f"team_name_{r['id']}", label_visibility="collapsed")
        grp = c2.text_input("Grupo", value=str(r["group_letter"]), key=f"team_group_{r['id']}", label_visibility="collapsed")
        if c3.button("Guardar", key=f"save_team_{r['id']}"):
            db.update_team(int(r["id"]), name, grp)
            st.success("Equipo actualizado.")
            rerun()
    st.markdown("### Añadir equipo")
    with st.form("add_team_form"):
        c1, c2 = st.columns([3, 1])
        name = c1.text_input("Nombre equipo")
        grp = c2.text_input("Grupo", value="A")
        if st.form_submit_button("Añadir"):
            db.add_team(tournament_id, name, grp)
            st.success("Equipo añadido.")
            rerun()


def render_tiebreakers_admin(tournament_id: int) -> None:
    st.subheader("Desempates manuales")
    st.caption("Menor número = mejor posición. Déjalo en 999 si no quieres forzar nada. La clasificación se recalcula después de guardar.")
    teams = load_teams(tournament_id)
    overrides = load_overrides(tournament_id)
    scope = st.selectbox("Ámbito", ["group", "third_places"], format_func=lambda x: "Grupo" if x == "group" else "Mejores terceros")
    if scope == "group":
        group = st.selectbox("Grupo", sorted(teams["group_letter"].unique().tolist()))
        df = teams[teams["group_letter"] == group]
    else:
        group = None
        df = teams
    for _, r in df.iterrows():
        current = 999
        sub = overrides[(overrides["scope"] == scope) & (overrides["team_id"] == int(r["id"]))]
        if group is not None:
            sub = sub[sub["group_letter"] == group]
        else:
            sub = sub[sub["group_letter"].isna()]
        if not sub.empty:
            current = int(sub.iloc[0]["manual_order"])
        c1, c2, c3 = st.columns([3, 1, 1])
        c1.write(r["name"])
        order = c2.number_input("Orden", min_value=1, max_value=999, value=current, key=f"tb_{scope}_{group}_{r['id']}", label_visibility="collapsed")
        if c3.button("Guardar", key=f"save_tb_{scope}_{group}_{r['id']}"):
            db.set_ranking_override(tournament_id, scope, group, int(r["id"]), int(order))
            st.success("Desempate guardado. Revisa la pestaña Clasificación fase de grupos.")
            st.cache_data.clear()
            rerun()


def render_import_export(tournament_id: int) -> None:
    st.subheader("Importar / exportar")
    st.markdown("### Plantilla calendario")
    template = pd.DataFrame([
        {"round_key": "grupos", "phase": "grupos", "group_letter": "A", "matchday": 1, "home_team": "Equipo 1", "away_team": "Equipo 2", "kickoff_datetime": "2026-06-11 21:00"}
    ])
    st.download_button("Descargar plantilla CSV", template.to_csv(index=False).encode("utf-8"), "plantilla_partidos.csv", "text/csv")
    uploaded = st.file_uploader("Importar partidos CSV", type=["csv"])
    clear = st.checkbox("Borrar partidos existentes antes de importar", value=False)
    if uploaded and st.button("Importar CSV"):
        data = pd.read_csv(uploaded)
        required = {"round_key", "phase", "home_team", "away_team"}
        if not required.issubset(set(data.columns)):
            st.error(f"Faltan columnas obligatorias: {required}")
            return
        if clear:
            db.execute("DELETE FROM matches WHERE tournament_id=?", [tournament_id])
        for _, row in data.iterrows():
            group = str(row.get("group_letter", "")).strip().upper() if pd.notna(row.get("group_letter")) else None
            h_id = db.get_team_id(tournament_id, str(row["home_team"])) or db.add_team(tournament_id, str(row["home_team"]), group or "Z")
            a_id = db.get_team_id(tournament_id, str(row["away_team"])) or db.add_team(tournament_id, str(row["away_team"]), group or "Z")
            md = safe_int(row.get("matchday"), None)
            db.add_match(tournament_id, str(row["round_key"]), str(row["phase"]), group, md, h_id, a_id, str(row.get("kickoff_datetime", "")) or None, "import")
        st.success("CSV importado.")
        rerun()

    st.markdown("### Exportar backup")
    if st.button("Generar backup Excel"):
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for name, sql in {
                "teams": "SELECT * FROM teams WHERE tournament_id=?",
                "matches": "SELECT * FROM matches WHERE tournament_id=?",
                "participants": "SELECT * FROM participants WHERE tournament_id=?",
                "predictions": "SELECT * FROM predictions WHERE tournament_id=?",
                "rounds": "SELECT * FROM rounds WHERE tournament_id=?",
                "scoring_rules": "SELECT * FROM scoring_rules WHERE tournament_id=?",
            }.items():
                db.query_df(sql, [tournament_id]).to_excel(writer, sheet_name=name, index=False)
        st.download_button("Descargar backup", output.getvalue(), "backup_porra_martinotes.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def render_prediction_match(m, participant_id: int, tournament_id: int, scope: str, disabled: bool, teams: pd.DataFrame) -> tuple:
    mid = int(m["id"])
    pred = db.get_one(
        "SELECT * FROM predictions WHERE tournament_id=? AND participant_id=? AND match_id=? AND scope=?",
        [tournament_id, participant_id, mid, scope],
    )
    default_h = safe_int(pred["predicted_home_goals"] if pred else None, None)
    default_a = safe_int(pred["predicted_away_goals"] if pred else None, None)
    with st.container(border=True):
        left, gh, dash, ga, right, extra = st.columns([3, 1, 0.2, 1, 3, 3])
        left.markdown(f"**{m['home_team']}**")
        hg = gh.number_input("GL", min_value=0, max_value=30, value=default_h, key=f"pred_h_{scope}_{mid}", label_visibility="collapsed", disabled=disabled)
        dash.markdown("### -")
        ag = ga.number_input("GV", min_value=0, max_value=30, value=default_a, key=f"pred_a_{scope}_{mid}", label_visibility="collapsed", disabled=disabled)
        right.markdown(f"**{m['away_team']}**")
        if str(m["round_key"]) != "grupos":
            et = extra.checkbox("Prórroga", value=bool(pred["predicted_extra_time"]) if pred else False, key=f"pred_et_{scope}_{mid}", disabled=disabled)
            pen = extra.checkbox("Penaltis", value=bool(pred["predicted_penalties"]) if pred else False, key=f"pred_pen_{scope}_{mid}", disabled=disabled)
            winner_ids = [int(m["home_team_id"]), int(m["away_team_id"])]
            default_winner = safe_int(pred["predicted_winner_team_id"] if pred else None, winner_ids[0])
            team_names = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
            winner = extra.selectbox(
                "Pasa",
                winner_ids,
                index=winner_ids.index(default_winner) if default_winner in winner_ids else 0,
                format_func=lambda x: team_names.get(int(x), str(x)),
                key=f"pred_winner_{scope}_{mid}",
                disabled=disabled,
            )
        else:
            et, pen, winner = False, False, None
    return mid, hg, ag, winner, et, pen


def render_predictions(tournament_id: int, participant_id: int) -> None:
    st.subheader("Predicciones")
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    predictions = load_predictions(tournament_id)
    tabs = st.tabs(["Inicial", "Ronda de 32", "Octavos", "Cuartos", "Semifinales", "Final"])

    with tabs[0]:
        st.markdown("### Predicción inicial · fase de grupos")
        group_matches = matches[matches["round_key"] == "grupos"].copy()
        complete, total = completed_predictions_count(
            predictions[(predictions["participant_id"] == participant_id) & (predictions["scope"] == "initial")],
            group_matches,
        )
        st.progress(0 if total == 0 else complete / total, text=f"Progreso: {complete}/{total} partidos completados")
        locked = is_round_locked(tournament_id, "grupos")
        if locked:
            st.warning("La fase de grupos está cerrada. Puedes ver tus apuestas, pero no editarlas.")
        mdays = sorted([int(x) for x in group_matches["matchday"].dropna().unique().tolist()])
        day = st.selectbox("Jornada", mdays if mdays else [1], key="pred_initial_matchday")
        day_matches = group_matches[group_matches["matchday"] == day]
        to_save = []
        for group, block in day_matches.groupby("group_letter", sort=True):
            st.markdown(f"#### Grupo {group}")
            for _, m in block.iterrows():
                to_save.append(render_prediction_match(m, participant_id, tournament_id, "initial", locked, teams))
        if st.button("Guardar jornada", type="primary", disabled=locked, key="save_initial_preds"):
            if is_round_locked(tournament_id, "grupos"):
                st.error("No se puede guardar: ronda cerrada.")
            else:
                with db.defer_sheets_sync():
                    for mid, hg, ag, winner, et, pen in to_save:
                        db.upsert_prediction(tournament_id, participant_id, mid, hg, ag, "initial", "grupos", winner, et, pen, True)
                st.success("Predicciones guardadas.")
                rerun()

    for tab, rk in zip(tabs[1:], KNOCKOUT_ROUNDS):
        with tab:
            st.markdown(f"### Predicción · {get_round_name(rk)}")
            rk_matches = matches[matches["round_key"] == rk].copy()
            if rk_matches.empty:
                st.info("Todavía no hay partidos pintados para esta ronda.")
                continue
            locked = is_round_locked(tournament_id, rk)
            if locked:
                st.warning("Esta ronda está cerrada. Puedes ver tus apuestas, pero no editarlas.")
            to_save = []
            for _, m in rk_matches.iterrows():
                to_save.append(render_prediction_match(m, participant_id, tournament_id, rk, locked, teams))
            if st.button(f"Guardar {get_round_name(rk)}", type="primary", disabled=locked, key=f"save_preds_{rk}"):
                if is_round_locked(tournament_id, rk):
                    st.error("No se puede guardar: ronda cerrada.")
                else:
                    with db.defer_sheets_sync():
                        for mid, hg, ag, winner, et, pen in to_save:
                            db.upsert_prediction(tournament_id, participant_id, mid, hg, ag, rk, rk, winner, et, pen, False)
                    st.success("Predicciones guardadas.")
                    rerun()


def render_leaderboard(tournament_id: int) -> None:
    st.subheader("Clasificación general")
    rules = db.get_rules(tournament_id)
    st.info("La puntuación base será sobre 1.000 puntos. Podrán existir bonus adicionales por acertar eliminatorias desde la predicción inicial.")
    participants = load_participants(tournament_id)
    if participants.empty:
        st.warning("Todavía no hay jugadores registrados.")
        return
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    predictions = load_predictions(tournament_id)
    overrides = load_overrides(tournament_id)
    lb, details = build_leaderboard(participants, matches, teams, predictions, overrides, rules)
    st.dataframe(lb[["Pos", "Jugador", "Total", "Grupos", "Eliminatorias", "Extras", "Bonus"]], hide_index=True, use_container_width=True)
    st.markdown("### Detalle por jugador")
    for _, row in lb.iterrows():
        pid = int(row["participant_id"])
        d = details[pid]
        with st.expander(f"{int(row['Pos'])}. {row['Jugador']} · {row['Total']} puntos"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Fase de grupos", f"{d['groups_total']:.2f}")
            c2.metric("Eliminatorias", f"{d['knockout_total']:.2f}")
            c3.metric("Extras", f"{d['extras']:.2f}")
            c4.metric("Bonus", f"{d['bonus']:.2f}")
            st.markdown("**Detalle grupos**")
            if d.get("completed_groups_count", 0) == 0:
                st.write("Posiciones: 0.00 · pendiente hasta que haya grupos completos")
            else:
                st.write(
                    f"Posiciones: {d['groups_positions']:.2f} · "
                    f"{d['positions_ok']}/{d['positions_total']} posiciones exactas "
                    f"({d.get('completed_groups_count', 0)} grupos completos evaluados)"
                )
            st.write(
                f"Signos: {d['groups_signs']:.2f} · "
                f"{d['signs_ok']}/{d['signs_total']} partidos posibles "
                f"({d.get('signs_evaluated', 0)} partidos reales evaluados)"
            )
            st.write(
                f"Marcadores exactos: {d['groups_exact']:.2f} · "
                f"{d['exact_ok']}/{d['exact_total']} partidos posibles "
                f"({d.get('exact_evaluated', 0)} partidos reales evaluados)"
            )
            st.markdown("**Detalle eliminatorias**")
            st.write(f"Quién pasa: {d['knockout_qualifier']:.2f}")
            st.write(f"Resultado/prórroga/penaltis: {d['knockout_result']:.2f}")


# ====== BLOQUE FINAL CONSOLIDADO: resultados, bracket, extras y scoring ======
EXTRA_ITEMS = [
    ("balon_oro", "Balón de Oro"),
    ("bota_oro", "Bota de Oro"),
    ("guante_oro", "Guante de Oro"),
    ("mejor_joven", "Mejor Jugador Joven"),
    ("equipo_entretenido", "Equipo más entretenido"),
    ("gol_torneo", "Gol del Torneo"),
]

NEXT_ROUND = {"ronda32": "octavos", "octavos": "cuartos", "cuartos": "semifinales", "semifinales": "final"}




REAL_NEXT_ROUND = {"ronda32": "octavos", "octavos": "cuartos", "cuartos": "semifinales", "semifinales": "final"}
ROUND_SLOT_PREFIX = {"ronda32": "R32", "octavos": "OCT", "cuartos": "QF", "semifinales": "SF", "final": "F"}


def _slot_number(value) -> int:
    text = str(value or "")
    digits = ""
    for ch in reversed(text):
        if ch.isdigit():
            digits = ch + digits
        elif digits:
            break
    return int(digits) if digits else 9999


def is_real_round_complete(matches: pd.DataFrame, round_key: str) -> tuple[bool, str]:
    rdf = matches[matches["round_key"] == round_key].copy()
    if rdf.empty:
        return False, f"No hay partidos en {get_round_name(round_key)}."
    pending = rdf[rdf["status"] != "played"]
    if not pending.empty:
        return False, f"Faltan {len(pending)} partidos por confirmar en {get_round_name(round_key)}."
    if round_key != "grupos":
        without_winner = rdf[rdf["winner_team_id"].isna()]
        if not without_winner.empty:
            return False, f"Faltan ganadores en {len(without_winner)} partidos de {get_round_name(round_key)}."
    return True, "OK"


def generate_next_real_round(tournament_id: int, matches: pd.DataFrame, current_round: str) -> tuple[bool, str]:
    next_round = REAL_NEXT_ROUND.get(current_round)
    if not next_round:
        return False, "No hay siguiente ronda para generar."

    ok, msg = is_real_round_complete(matches, current_round)
    if not ok:
        return False, msg

    existing_next = matches[matches["round_key"] == next_round]
    if not existing_next.empty:
        return False, f"Ya existen partidos de {get_round_name(next_round)}. No se duplican. Elimínalos manualmente si quieres regenerar."

    current = matches[matches["round_key"] == current_round].copy()
    current["_slot_order"] = current["bracket_slot"].apply(_slot_number)
    current = current.sort_values(["_slot_order", "id"])
    winners = [int(x) for x in current["winner_team_id"].dropna().tolist()]
    if len(winners) != len(current):
        return False, f"Faltan ganadores en {get_round_name(current_round)}."
    if len(winners) % 2 != 0:
        return False, f"Número impar de ganadores en {get_round_name(current_round)}."

    prefix = ROUND_SLOT_PREFIX.get(next_round, next_round.upper())
    created = 0
    for i in range(0, len(winners), 2):
        slot = i // 2 + 1
        db.add_match(
            tournament_id,
            next_round,
            next_round,
            None,
            None,
            winners[i],
            winners[i + 1],
            None,
            "auto_bracket",
            f"{prefix}-{slot}",
        )
        created += 1
    return True, f"{get_round_name(next_round)} generado: {created} partidos."


def load_bracket_predictions(tournament_id: int) -> pd.DataFrame:
    return db.query_df(
        """
        SELECT bp.*, ht.name AS home_team, at.name AS away_team, wt.name AS winner_team
        FROM bracket_predictions bp
        JOIN teams ht ON ht.id=bp.home_team_id
        JOIN teams at ON at.id=bp.away_team_id
        LEFT JOIN teams wt ON wt.id=bp.predicted_winner_team_id
        WHERE bp.tournament_id=?
        ORDER BY bp.participant_id,
            CASE bp.round_key WHEN 'ronda32' THEN 1 WHEN 'octavos' THEN 2 WHEN 'cuartos' THEN 3 WHEN 'semifinales' THEN 4 WHEN 'final' THEN 5 ELSE 99 END,
            bp.slot
        """,
        [tournament_id],
    )


def load_extra_predictions(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM extra_predictions WHERE tournament_id=?", [tournament_id])


def load_extra_validations(tournament_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM extra_validations WHERE tournament_id=?", [tournament_id])


def derive_winner_from_result(home_id: int, away_id: int, hg: int | None, ag: int | None, penalties: bool, selected_winner: int | None) -> int | None:
    if penalties:
        return selected_winner
    if hg is None or ag is None:
        return None
    if int(hg) > int(ag):
        return int(home_id)
    if int(ag) > int(hg):
        return int(away_id)
    return None


def _prediction_matches_as_results_local(matches_df: pd.DataFrame, predictions_df: pd.DataFrame) -> pd.DataFrame:
    pred = predictions_df.dropna(subset=["predicted_home_goals", "predicted_away_goals"]).copy()
    if matches_df.empty or pred.empty:
        return matches_df.iloc[0:0].copy()
    merged = matches_df.merge(pred[["match_id", "predicted_home_goals", "predicted_away_goals"]], left_on="id", right_on="match_id", how="inner")
    merged["home_goals"] = merged["predicted_home_goals"].astype(int)
    merged["away_goals"] = merged["predicted_away_goals"].astype(int)
    merged["status"] = "played"
    return merged


def build_auto_r32_pairings_from_standings(standings: pd.DataFrame) -> list[tuple[int, int]]:
    """Genera un cuadro R32 determinista y testeable.

    Criterios de clasificación:
    - posición de grupo calculada por puntos + enfrentamiento directo + DG + GF + orden alfabético;
    - terceros: puntos, DG, GF y orden alfabético.

    Asignación de cruces simplificada:
    - 8 primeros contra los 8 mejores terceros;
    - 4 primeros restantes contra 4 segundos;
    - 8 segundos restantes entre sí.
    """
    if standings.empty:
        return []
    firsts = standings[standings["Pos"] == 1].sort_values("Grupo")["team_id"].astype(int).tolist()
    seconds = standings[standings["Pos"] == 2].sort_values("Grupo")["team_id"].astype(int).tolist()
    thirds = standings[standings["Pos"] == 3].copy()
    thirds = thirds.sort_values(["Pts", "DG", "GF", "Equipo"], ascending=[False, False, False, True], kind="mergesort")["team_id"].astype(int).tolist()[:8]
    if len(firsts) < 12 or len(seconds) < 12 or len(thirds) < 8:
        return []
    pairings: list[tuple[int, int]] = []
    for i in range(8):
        pairings.append((firsts[i], thirds[i]))
    for i in range(4):
        pairings.append((firsts[8 + i], seconds[i]))
    remaining_seconds = seconds[4:12]
    for i in range(0, len(remaining_seconds), 2):
        if i + 1 < len(remaining_seconds):
            pairings.append((remaining_seconds[i], remaining_seconds[i + 1]))
    return pairings[:16]


def render_matches_results(tournament_id: int, admin: bool) -> None:
    st.subheader("Partidos y resultados" if admin else "Partidos/resultados")
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    if matches.empty:
        st.warning("No hay partidos cargados.")
        if admin:
            render_match_management(tournament_id, matches, teams)
        return
    if admin:
        render_match_management(tournament_id, matches, teams)
        st.divider()

    c1, c2, c3, c4 = st.columns(4)
    round_options = ["Todos"] + [get_round_name(x) for x in ROUND_KEYS]
    round_sel_label = c1.selectbox("Ronda", round_options, key=f"match_filter_round_final_{admin}")
    round_sel = "Todos" if round_sel_label == "Todos" else {v: k for k, v in ROUND_LABELS.items()}[round_sel_label]
    groups = ["Todos"] + sorted([x for x in matches["group_letter"].dropna().unique().tolist()])
    group_sel = c2.selectbox("Grupo", groups, key=f"match_filter_group_final_{admin}")
    jornadas = ["Todas"] + sorted([int(x) for x in matches["matchday"].dropna().unique().tolist()])
    matchday_sel = c3.selectbox("Jornada", jornadas, key=f"match_filter_day_final_{admin}")
    status_sel = c4.selectbox("Estado", ["Todos", "pending", "played"], key=f"match_filter_status_final_{admin}")

    df = matches.copy()
    if round_sel != "Todos":
        df = df[df["round_key"] == round_sel]
    if group_sel != "Todos":
        df = df[df["group_letter"] == group_sel]
    if matchday_sel != "Todas":
        df = df[df["matchday"] == matchday_sel]
    if status_sel != "Todos":
        df = df[df["status"] == status_sel]
    st.caption(f"Partidos visibles: {len(df)}")
    if df.empty:
        st.info("No hay partidos con esos filtros.")
        return

    team_options = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
    changes = []
    for (rk, md, grp), block in df.groupby(["round_key", "matchday", "group_letter"], dropna=False, sort=True):
        label = f"{get_round_name(str(rk))}"
        if pd.notna(md): label += f" · Jornada {int(md)}"
        if pd.notna(grp): label += f" · Grupo {grp}"
        st.markdown(f"### {label}")
        for _, m in block.iterrows():
            mid = int(m["id"])
            with st.container(border=True):
                if admin:
                    if str(m["round_key"]) == "grupos":
                        left, gh, dash, ga, right, conf = st.columns([3, 1, 0.2, 1, 3, 2])
                        left.markdown(f"**{m['home_team']}**")
                        hg = gh.number_input("GL", min_value=0, max_value=30, value=safe_int(m["home_goals"], 0), key=f"real_hg_final_{mid}", label_visibility="collapsed")
                        dash.markdown("### -")
                        ag = ga.number_input("GV", min_value=0, max_value=30, value=safe_int(m["away_goals"], 0), key=f"real_ag_final_{mid}", label_visibility="collapsed")
                        right.markdown(f"**{m['away_team']}**")
                        confirmed = conf.checkbox("Resultado confirmado", value=str(m["status"]) == "played", key=f"real_confirm_final_{mid}")
                        changes.append((mid, hg, ag, confirmed, False, False, None, int(m["home_team_id"]), int(m["away_team_id"])))
                    else:
                        top = st.columns([3, 1, 1, 3, 3])
                        top[0].markdown(f"**{m['home_team']}**")
                        pen = top[4].checkbox("Penaltis", value=bool(m["penalties"]), key=f"real_pen_final_{mid}")
                        et = top[4].checkbox("Prórroga", value=bool(m["extra_time"]), key=f"real_et_final_{mid}")
                        if pen:
                            top[1].caption("Marcador oculto")
                            top[2].caption("por penaltis")
                            hg, ag = 0, 0
                            winner_ids = [int(m["home_team_id"]), int(m["away_team_id"])]
                            default_winner = safe_int(m["winner_team_id"], winner_ids[0])
                            winner = top[4].selectbox("¿Quién pasa?", winner_ids, index=winner_ids.index(default_winner) if default_winner in winner_ids else 0, format_func=lambda x: team_options.get(int(x), str(x)), key=f"real_winner_final_{mid}")
                        else:
                            hg = top[1].number_input("GL", min_value=0, max_value=30, value=safe_int(m["home_goals"], 0), key=f"real_hg_final_{mid}", label_visibility="collapsed")
                            top[2].markdown("### -")
                            ag = top[3].number_input("GV", min_value=0, max_value=30, value=safe_int(m["away_goals"], 0), key=f"real_ag_final_{mid}", label_visibility="collapsed")
                            winner = derive_winner_from_result(int(m["home_team_id"]), int(m["away_team_id"]), hg, ag, False, None)
                            if hg == ag:
                                top[4].warning("Empate sin penaltis: no se puede deducir quién pasa.")
                        top[3].markdown(f"**{m['away_team']}**")
                        confirmed = top[4].checkbox("Resultado confirmado", value=str(m["status"]) == "played", key=f"real_confirm_final_{mid}")
                        changes.append((mid, hg, ag, confirmed, bool(et), bool(pen), winner, int(m["home_team_id"]), int(m["away_team_id"])))
                else:
                    st.markdown(f"**{m['home_team']}** {'' if pd.isna(m['home_goals']) else int(m['home_goals'])} - {'' if pd.isna(m['away_goals']) else int(m['away_goals'])} **{m['away_team']}**")
                    if str(m["round_key"]) != "grupos" and pd.notna(m.get("winner_team")):
                        st.caption(f"Pasa: {m['winner_team']}")
    if admin and st.button("Guardar todos los resultados visibles", type="primary", key="save_all_visible_results_final"):
        saved = 0
        errors = []
        for mid, hg, ag, confirmed, et, pen, winner, home_id, away_id in changes:
            if not confirmed:
                db.update_match_result(mid, None, None, None, False, False, None)
                saved += 1
                continue
            if pen and winner is None:
                errors.append(f"Partido #{mid}: si hay penaltis debes indicar quién pasa.")
                continue
            if not pen and str(load_matches(tournament_id).loc[load_matches(tournament_id)['id']==mid, 'round_key'].iloc[0]) != "grupos" and hg == ag:
                errors.append(f"Partido #{mid}: empate en eliminatoria sin penaltis.")
                continue
            winner_id = None if str(load_matches(tournament_id).loc[load_matches(tournament_id)['id']==mid, 'round_key'].iloc[0]) == "grupos" else derive_winner_from_result(home_id, away_id, hg, ag, pen, winner)
            db.update_match_result(mid, int(hg), int(ag), winner_id, et, pen, "penaltis" if pen else ("prorroga" if et else "normal"))
            saved += 1
        if errors:
            st.error("No se guardaron algunos partidos:\n" + "\n".join(errors))
        st.success(f"Guardados/actualizados {saved} partidos visibles.")
        st.cache_data.clear()
        rerun()


def render_prediction_match(m, participant_id: int, tournament_id: int, scope: str, disabled: bool, teams: pd.DataFrame) -> tuple:
    mid = int(m["id"])
    pred = db.get_one("SELECT * FROM predictions WHERE tournament_id=? AND participant_id=? AND match_id=? AND scope=?", [tournament_id, participant_id, mid, scope])
    default_h = safe_int(pred["predicted_home_goals"] if pred else None, None)
    default_a = safe_int(pred["predicted_away_goals"] if pred else None, None)
    with st.container(border=True):
        if str(m["round_key"]) == "grupos":
            left, gh, dash, ga, right = st.columns([3, 1, 0.2, 1, 3])
            left.markdown(f"**{m['home_team']}**")
            hg = gh.number_input("GL", min_value=0, max_value=30, value=default_h, key=f"pred_h_final_{scope}_{mid}", label_visibility="collapsed", disabled=disabled)
            dash.markdown("### -")
            ag = ga.number_input("GV", min_value=0, max_value=30, value=default_a, key=f"pred_a_final_{scope}_{mid}", label_visibility="collapsed", disabled=disabled)
            right.markdown(f"**{m['away_team']}**")
            return mid, hg, ag, None, False, False
        else:
            c = st.columns([3, 1, 1, 3, 3])
            c[0].markdown(f"**{m['home_team']}**")
            team_names = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
            pen = c[4].checkbox("Penaltis", value=bool(pred["predicted_penalties"]) if pred else False, key=f"pred_pen_final_{scope}_{mid}", disabled=disabled)
            et = c[4].checkbox("Prórroga", value=bool(pred["predicted_extra_time"]) if pred else False, key=f"pred_et_final_{scope}_{mid}", disabled=disabled)
            if pen:
                c[1].caption("Resultado oculto")
                c[2].caption("penaltis")
                hg, ag = 0, 0
                winner_ids = [int(m["home_team_id"]), int(m["away_team_id"])]
                default_winner = safe_int(pred["predicted_winner_team_id"] if pred else None, winner_ids[0])
                winner = c[4].selectbox("¿Quién pasa?", winner_ids, index=winner_ids.index(default_winner) if default_winner in winner_ids else 0, format_func=lambda x: team_names.get(int(x), str(x)), key=f"pred_winner_final_{scope}_{mid}", disabled=disabled)
            else:
                hg = c[1].number_input("GL", min_value=0, max_value=30, value=default_h, key=f"pred_h_final_{scope}_{mid}", label_visibility="collapsed", disabled=disabled)
                c[2].markdown("### -")
                ag = c[3].number_input("GV", min_value=0, max_value=30, value=default_a, key=f"pred_a_final_{scope}_{mid}", label_visibility="collapsed", disabled=disabled)
                winner = derive_winner_from_result(int(m["home_team_id"]), int(m["away_team_id"]), hg, ag, False, None)
                if hg is not None and ag is not None and hg == ag:
                    c[4].warning("Si empata, marca penaltis para indicar quién pasa.")
            c[3].markdown(f"**{m['away_team']}**")
            return mid, hg, ag, winner, et, pen


def render_bracket_prediction_row(row, scope: str, disabled: bool, teams: pd.DataFrame) -> tuple:
    bid = int(row["id"])
    team_names = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
    with st.container(border=True):
        c = st.columns([3, 1, 1, 3, 3])
        c[0].markdown(f"**{row['home_team']}**")
        pen = c[4].checkbox("Penaltis", value=bool(row["predicted_penalties"]), key=f"br_pen_{scope}_{bid}", disabled=disabled)
        et = c[4].checkbox("Prórroga", value=bool(row["predicted_extra_time"]), key=f"br_et_{scope}_{bid}", disabled=disabled)
        if pen:
            c[1].caption("Resultado oculto")
            c[2].caption("penaltis")
            hg, ag = 0, 0
            winner_ids = [int(row["home_team_id"]), int(row["away_team_id"])]
            default_winner = safe_int(row["predicted_winner_team_id"], winner_ids[0])
            winner = c[4].selectbox("¿Quién pasa?", winner_ids, index=winner_ids.index(default_winner) if default_winner in winner_ids else 0, format_func=lambda x: team_names.get(int(x), str(x)), key=f"br_winner_{scope}_{bid}", disabled=disabled)
        else:
            hg = c[1].number_input("GL", 0, 30, safe_int(row["predicted_home_goals"], None), key=f"br_h_{scope}_{bid}", label_visibility="collapsed", disabled=disabled)
            c[2].markdown("### -")
            ag = c[3].number_input("GV", 0, 30, safe_int(row["predicted_away_goals"], None), key=f"br_a_{scope}_{bid}", label_visibility="collapsed", disabled=disabled)
            winner = derive_winner_from_result(int(row["home_team_id"]), int(row["away_team_id"]), hg, ag, False, None)
            if hg is not None and ag is not None and hg == ag:
                c[4].warning("Si empata, marca penaltis para indicar quién pasa.")
        c[3].markdown(f"**{row['away_team']}**")
    return bid, int(row["slot"]), int(row["home_team_id"]), int(row["away_team_id"]), hg, ag, winner, et, pen


def render_initial_bracket(tournament_id: int, participant_id: int, teams: pd.DataFrame) -> None:
    st.markdown("### Cuadro inicial automático")
    st.caption("Se genera desde tus resultados de fase de grupos. Este cuadro solo sirve para bonus inicial, no para la puntuación estándar de cada ronda.")
    group_matches = load_matches(tournament_id)
    group_matches = group_matches[group_matches["round_key"] == "grupos"].copy()
    preds = load_predictions(tournament_id)
    player_preds = preds[(preds["participant_id"] == participant_id) & (preds["scope"] == "initial")]
    pred_match_ids = set(player_preds.dropna(subset=["predicted_home_goals", "predicted_away_goals"])["match_id"].astype(int).tolist())
    all_group_ids = set(group_matches["id"].astype(int).tolist())
    if not all_group_ids.issubset(pred_match_ids):
        st.warning("Completa todos los partidos de fase de grupos para generar el cuadro inicial.")
        return
    locked = is_round_locked(tournament_id, "grupos")
    pred_as_results = _prediction_matches_as_results_local(group_matches, player_preds)
    standings = compute_group_standings(pred_as_results, teams, pd.DataFrame())
    pairings = build_auto_r32_pairings_from_standings(standings)
    if st.button("Generar/actualizar Ronda de 32 inicial", disabled=locked, key="generate_initial_r32"):
        if len(pairings) != 16:
            st.error("No se pudo generar R32: faltan clasificados.")
        else:
            with db.defer_sheets_sync():
                db.clear_bracket_round(tournament_id, participant_id, "initial", "ronda32")
                for i, (h, a) in enumerate(pairings, start=1):
                    db.upsert_bracket_prediction(tournament_id, participant_id, "initial", "ronda32", i, h, a)
            st.success("Ronda de 32 inicial generada.")
            rerun()
    bracket = load_bracket_predictions(tournament_id)
    bracket = bracket[(bracket["participant_id"] == participant_id) & (bracket["scope"] == "initial")]
    if bracket.empty:
        return
    team_names = {int(r["id"]): str(r["name"]) for _, r in teams.iterrows()}
    for rk in KNOCKOUT_ROUNDS:
        rdf = bracket[bracket["round_key"] == rk].copy()
        if rdf.empty:
            continue
        st.markdown(f"#### {get_round_name(rk)}")
        winners_saved = int(rdf["predicted_winner_team_id"].notna().sum())
        st.caption(f"Ganadores guardados: {winners_saved}/{len(rdf)}")
        to_save = []
        for _, row in rdf.iterrows():
            to_save.append(render_bracket_prediction_row(row, "initial", locked, teams))
        c1, c2 = st.columns(2)
        if c1.button(f"Guardar {get_round_name(rk)} inicial", disabled=locked, key=f"save_initial_br_{rk}"):
            errors = []
            valid_rows = []
            for bid, slot, home_id, away_id, hg, ag, winner, et, pen in to_save:
                if hg is None or ag is None:
                    errors.append(f"Slot {slot}: debes introducir marcador.")
                elif pen and winner is None:
                    errors.append(f"Slot {slot}: debes indicar quién pasa.")
                elif (not pen) and hg == ag:
                    errors.append(f"Slot {slot}: empate sin penaltis.")
                else:
                    valid_rows.append((slot, home_id, away_id, int(hg), int(ag), winner, et, pen))
            if errors:
                st.error("\n".join(errors))
            else:
                with db.defer_sheets_sync():
                    for slot, home_id, away_id, hg, ag, winner, et, pen in valid_rows:
                        db.upsert_bracket_prediction(tournament_id, participant_id, "initial", rk, slot, home_id, away_id, hg, ag, winner, et, pen)
                st.success("Cuadro guardado.")
                rerun()
        if rk in NEXT_ROUND and c2.button(f"Generar {get_round_name(NEXT_ROUND[rk])} inicial", disabled=locked, key=f"gen_next_initial_{rk}"):
            current = load_bracket_predictions(tournament_id)
            current = current[(current["participant_id"] == participant_id) & (current["scope"] == "initial") & (current["round_key"] == rk)].sort_values("slot")
            winners = [int(x) for x in current["predicted_winner_team_id"].dropna().tolist()]
            if len(winners) != len(current):
                st.error("Primero guarda todos los ganadores de esta ronda.")
            else:
                nr = NEXT_ROUND[rk]
                existing_next = load_bracket_predictions(tournament_id)
                existing_next = existing_next[(existing_next["participant_id"] == participant_id) & (existing_next["scope"] == "initial") & (existing_next["round_key"] == nr)]
                if not existing_next.empty:
                    st.warning(f"Ya existía {get_round_name(nr)} inicial. Se reemplaza por los ganadores actuales.")
                with db.defer_sheets_sync():
                    db.clear_bracket_round(tournament_id, participant_id, "initial", nr)
                    for i in range(0, len(winners), 2):
                        if i + 1 < len(winners):
                            db.upsert_bracket_prediction(tournament_id, participant_id, "initial", nr, i//2 + 1, winners[i], winners[i + 1])
                st.success(f"{get_round_name(nr)} inicial generado.")
                rerun()


def render_predictions(tournament_id: int, participant_id: int) -> None:
    st.subheader("Predicciones")
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    predictions = load_predictions(tournament_id)
    tabs = st.tabs(["Inicial", "Ronda de 32", "Octavos", "Cuartos", "Semifinales", "Final", "Extras"])
    with tabs[0]:
        st.markdown("### Predicción inicial · fase de grupos")
        group_matches = matches[matches["round_key"] == "grupos"].copy()
        complete, total = completed_predictions_count(predictions[(predictions["participant_id"] == participant_id) & (predictions["scope"] == "initial")], group_matches)
        st.progress(0 if total == 0 else complete / total, text=f"Progreso: {complete}/{total} partidos completados")
        locked = is_round_locked(tournament_id, "grupos")
        if locked: st.warning("La fase de grupos está cerrada. Puedes ver tus apuestas, pero no editarlas.")
        mdays = sorted([int(x) for x in group_matches["matchday"].dropna().unique().tolist()])
        day = st.selectbox("Jornada", mdays if mdays else [1], key="pred_initial_matchday_final")
        day_matches = group_matches[group_matches["matchday"] == day]
        to_save = []
        for group, block in day_matches.groupby("group_letter", sort=True):
            st.markdown(f"#### Grupo {group}")
            for _, m in block.iterrows():
                to_save.append(render_prediction_match(m, participant_id, tournament_id, "initial", locked, teams))
        if st.button("Guardar jornada", type="primary", disabled=locked, key="save_initial_preds_final"):
            if is_round_locked(tournament_id, "grupos"):
                st.error("No se puede guardar: ronda cerrada.")
            else:
                with db.defer_sheets_sync():
                    for mid, hg, ag, winner, et, pen in to_save:
                        db.upsert_prediction(tournament_id, participant_id, mid, hg, ag, "initial", "grupos", winner, et, pen, True)
                st.success("Predicciones guardadas.")
                rerun()
        st.divider()
        render_initial_bracket(tournament_id, participant_id, teams)
    for tab, rk in zip(tabs[1:6], KNOCKOUT_ROUNDS):
        with tab:
            st.markdown(f"### Predicción estándar · {get_round_name(rk)}")
            st.caption("Estos puntos son los de la ronda estándar. La predicción inicial solo genera bonus extra.")
            rk_matches = matches[matches["round_key"] == rk].copy()
            if rk_matches.empty:
                st.info("Todavía no hay partidos pintados para esta ronda.")
                continue
            locked = is_round_locked(tournament_id, rk)
            if locked: st.warning("Esta ronda está cerrada. Puedes ver tus apuestas, pero no editarlas.")
            to_save = [render_prediction_match(m, participant_id, tournament_id, rk, locked, teams) for _, m in rk_matches.iterrows()]
            if st.button(f"Guardar {get_round_name(rk)}", type="primary", disabled=locked, key=f"save_preds_final_{rk}"):
                errors = []
                valid_rows = []
                for mid, hg, ag, winner, et, pen in to_save:
                    if hg is None or ag is None:
                        errors.append(f"Partido #{mid}: debes introducir marcador.")
                    elif pen and winner is None:
                        errors.append(f"Partido #{mid}: debes indicar quién pasa.")
                    elif not pen and hg == ag:
                        errors.append(f"Partido #{mid}: empate sin penaltis.")
                    else:
                        valid_rows.append((mid, int(hg), int(ag), winner, et, pen))
                if errors:
                    st.error("\n".join(errors))
                else:
                    with db.defer_sheets_sync():
                        for mid, hg, ag, winner, et, pen in valid_rows:
                            db.upsert_prediction(tournament_id, participant_id, mid, hg, ag, rk, rk, winner, et, pen, False)
                    st.success("Predicciones guardadas.")
                    rerun()
    with tabs[6]:
        render_player_extras(tournament_id, participant_id)


def render_player_extras(tournament_id: int, participant_id: int) -> None:
    st.markdown("### Extras")
    st.caption("Texto libre. El admin validará manualmente al final del Mundial si acertaste cada ítem.")
    existing = load_extra_predictions(tournament_id)
    existing = existing[existing["participant_id"] == participant_id]
    values = {r["extra_key"]: str(r["predicted_value"] or "") for _, r in existing.iterrows()}
    locked = is_round_locked(tournament_id, "grupos")
    inputs = {}
    for key, label in EXTRA_ITEMS:
        inputs[key] = st.text_input(label, value=values.get(key, ""), key=f"extra_pred_{key}", disabled=locked)
    if st.button("Guardar extras", type="primary", disabled=locked, key="save_player_extras"):
        with db.defer_sheets_sync():
            for key, value in inputs.items():
                db.upsert_extra_prediction(tournament_id, participant_id, key, value)
        st.success("Extras guardados.")
        rerun()


def render_admin_extras(tournament_id: int) -> None:
    st.subheader("Validación de extras")
    st.caption("El jugador escribe texto libre; el admin marca manualmente si acertó o no.")
    participants = load_participants(tournament_id)
    preds = load_extra_predictions(tournament_id)
    vals = load_extra_validations(tournament_id)
    if participants.empty:
        st.info("No hay jugadores.")
        return
    changes = []
    for _, p in participants.iterrows():
        with st.expander(str(p["name"]), expanded=False):
            pid = int(p["id"])
            p_preds = preds[preds["participant_id"] == pid]
            p_vals = vals[vals["participant_id"] == pid]
            for key, label in EXTRA_ITEMS:
                pred_value = ""
                row = p_preds[p_preds["extra_key"] == key]
                if not row.empty:
                    pred_value = str(row.iloc[0]["predicted_value"] or "")
                current = False
                vrow = p_vals[p_vals["extra_key"] == key]
                if not vrow.empty:
                    current = bool(int(vrow.iloc[0]["is_correct"]))
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{label}:** {pred_value if pred_value else '—'}")
                ok = c2.checkbox("Acertó", value=current, key=f"extra_val_{pid}_{key}")
                changes.append((pid, key, ok))
    if st.button("Guardar validación de extras", type="primary"):
        with db.defer_sheets_sync():
            for pid, key, ok in changes:
                db.set_extra_validation(tournament_id, pid, key, ok)
        st.success("Validación guardada.")
        rerun()


def render_bracket_admin(tournament_id: int) -> None:
    st.subheader("Bracket / Eliminatorias")
    st.caption("Genera la Ronda de 32 desde grupos y, después, cada ronda desde los ganadores confirmados de la ronda anterior.")
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    overrides = load_overrides(tournament_id)
    group_matches = matches[matches["round_key"] == "grupos"]
    standings = compute_group_standings(group_matches, teams, overrides)

    groups_complete, groups_msg = is_real_round_complete(matches, "grupos")
    if not groups_complete:
        st.warning(f"Para generar Ronda de 32: {groups_msg}")

    if st.button("Generar Ronda de 32 desde clasificación de grupos", type="primary"):
        if not groups_complete:
            st.error(groups_msg)
        else:
            pairings = build_auto_r32_pairings_from_standings(standings)
            if len(pairings) != 16:
                st.error("No se puede generar: faltan grupos o clasificados.")
            else:
                existing_r32 = matches[matches["round_key"] == "ronda32"]
                if not existing_r32.empty:
                    st.warning("Ya había partidos de Ronda de 32. No se borran automáticamente. Elimínalos si quieres regenerar limpio.")
                else:
                    for i, (h, a) in enumerate(pairings, start=1):
                        db.add_match(tournament_id, "ronda32", "ronda32", None, None, h, a, None, "auto_bracket", f"R32-{i}")
                    st.success("Ronda de 32 generada.")
                    rerun()

    st.divider()
    current_matches = load_matches(tournament_id)
    for rk in KNOCKOUT_ROUNDS:
        rdf = current_matches[current_matches["round_key"] == rk].copy()
        st.markdown(f"### {get_round_name(rk)}")
        if rdf.empty:
            st.info("Sin partidos.")
            continue

        complete, complete_msg = is_real_round_complete(current_matches, rk)
        if rk in REAL_NEXT_ROUND:
            c1, c2 = st.columns([2, 4])
            with c1:
                if st.button(f"Generar {get_round_name(REAL_NEXT_ROUND[rk])}", key=f"gen_real_next_{rk}"):
                    ok, msg = generate_next_real_round(tournament_id, current_matches, rk)
                    if ok:
                        st.success(msg)
                        rerun()
                    else:
                        st.error(msg)
            with c2:
                if complete:
                    st.success(f"{get_round_name(rk)} completa. Puedes generar {get_round_name(REAL_NEXT_ROUND[rk])}.")
                else:
                    st.warning(complete_msg)

        rdf["_slot_order"] = rdf["bracket_slot"].apply(_slot_number)
        rdf = rdf.sort_values(["_slot_order", "id"])
        for _, m in rdf.iterrows():
            result = "pendiente"
            if str(m["status"]) == "played":
                if bool(m["penalties"]):
                    result = f"penaltis · pasa {m['winner_team']}"
                else:
                    result = f"{int(m['home_goals'])}-{int(m['away_goals'])} · pasa {m['winner_team'] if pd.notna(m['winner_team']) else '—'}"
            st.write(f"#{int(m['id'])} · **{m['home_team']}** vs **{m['away_team']}** · {result}")


def render_leaderboard(tournament_id: int) -> None:
    st.subheader("Clasificación general")
    rules = db.get_rules(tournament_id)
    st.info("Puntuación base sobre 1.000 puntos. Puede haber bonus adicionales por predicción inicial.")
    participants = load_participants(tournament_id)
    if participants.empty:
        st.warning("Todavía no hay jugadores registrados.")
        return
    matches = load_matches(tournament_id)
    teams = load_teams(tournament_id)
    predictions = load_predictions(tournament_id)
    overrides = load_overrides(tournament_id)
    bracket = load_bracket_predictions(tournament_id)
    extra_vals = load_extra_validations(tournament_id)
    lb, details = build_leaderboard(participants, matches, teams, predictions, overrides, rules, bracket, extra_vals)
    st.dataframe(lb[["Pos", "Jugador", "Total", "Grupos", "Eliminatorias", "Extras", "Bonus"]], hide_index=True, use_container_width=True)
    st.markdown("### Detalle por jugador")
    for _, row in lb.iterrows():
        pid = int(row["participant_id"])
        d = details[pid]
        with st.expander(f"{int(row['Pos'])}. {row['Jugador']} · {row['Total']} puntos"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Fase de grupos", f"{d['groups_total']:.2f}")
            c2.metric("Eliminatorias", f"{d['knockout_total']:.2f}")
            c3.metric("Extras", f"{d['extras']:.2f}")
            c4.metric("Bonus", f"{d['bonus']:.2f}")
            st.markdown("**Detalle grupos**")
            st.write(f"Posiciones: {d['groups_positions']:.2f} · {d['positions_ok']}/{d['positions_total']} posiciones posibles")
            st.write(f"Signos: {d['groups_signs']:.2f} · {d['signs_ok']}/{d['signs_total']} partidos posibles")
            st.write(f"Marcadores exactos: {d['groups_exact']:.2f} · {d['exact_ok']}/{d['exact_total']} partidos posibles")
            st.markdown("**Detalle eliminatorias**")
            st.write(f"Quién pasa: {d['knockout_qualifier']:.2f}")
            st.write(f"Resultado/prórroga/penaltis: {d['knockout_result']:.2f}")
            st.markdown("**Extras y bonus**")
            st.write(f"Extras: {d['extras']:.2f}")
            st.write(f"Bonus inicial: {d['bonus']:.2f}")


def admin_view() -> None:
    tournament_id = int(st.session_state.tournament_id)
    logout_button()
    st.title("Panel administrador")
    tabs = st.tabs([
        "Rondas",
        "Partidos/resultados",
        "Clasificación fase de grupos",
        "Bracket",
        "Clasificación general",
        "Reglas puntuación",
        "Extras",
        "Equipos",
        "Importar/Exportar",
    ])
    with tabs[0]: render_rounds_admin(tournament_id)
    with tabs[1]: render_matches_results(tournament_id, admin=True)
    with tabs[2]: render_classifications(tournament_id)
    with tabs[3]: render_bracket_admin(tournament_id)
    with tabs[4]: render_leaderboard(tournament_id)
    with tabs[5]: render_score_rules(tournament_id)
    with tabs[6]: render_admin_extras(tournament_id)
    with tabs[7]: render_teams_admin(tournament_id)
    with tabs[8]: render_import_export(tournament_id)


def player_view() -> None:
    tournament_id = int(st.session_state.tournament_id)
    participant_id = int(st.session_state.participant_id)
    logout_button()
    st.title(f"Hola, {st.session_state.participant_name}")
    tabs = st.tabs(["Predicciones", "Partidos/resultados", "Clasificación fase de grupos", "Clasificación general"])
    with tabs[0]: render_predictions(tournament_id, participant_id)
    with tabs[1]: render_matches_results(tournament_id, admin=False)
    with tabs[2]: render_classifications(tournament_id)
    with tabs[3]: render_leaderboard(tournament_id)


def main() -> None:
    db.init_db()
    db.seed_default_tournament()
    ensure_session()
    if st.session_state.role == "admin" and st.session_state.tournament_id:
        admin_view()
    elif st.session_state.role == "player" and st.session_state.tournament_id and st.session_state.participant_id:
        player_view()
    else:
        login_view()


if __name__ == "__main__":
    main()
