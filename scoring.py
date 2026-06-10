from __future__ import annotations

import pandas as pd

KNOCKOUT_ROUNDS = ["ronda32", "octavos", "cuartos", "semifinales", "final"]
EXTRA_RULE_KEYS = [
    "extra_balon_oro",
    "extra_bota_oro",
    "extra_guante_oro",
    "extra_mejor_joven",
    "extra_equipo_entretenido",
    "extra_gol_torneo",
]


def safe_int(x, default=None):
    try:
        if pd.isna(x):
            return default
        return int(x)
    except Exception:
        return default


def safe_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def rule(rules: dict, key: str, default: float = 0.0) -> float:
    return safe_float(rules.get(key, default), default)


def match_sign(h: int | None, a: int | None) -> str | None:
    if h is None or a is None:
        return None
    if h > a:
        return "1"
    if a > h:
        return "2"
    return "X"


def compute_group_standings(matches: pd.DataFrame, teams: pd.DataFrame, overrides: pd.DataFrame | None = None) -> pd.DataFrame:
    if teams.empty:
        return pd.DataFrame()
    played = matches[(matches["round_key"] == "grupos") & (matches["status"] == "played")].copy() if not matches.empty else pd.DataFrame()
    rows = []
    for _, t in teams.iterrows():
        tid = int(t["id"])
        group = str(t["group_letter"])
        stats = {"team_id": tid, "Equipo": str(t["name"]), "Grupo": group, "PJ": 0, "PG": 0, "PE": 0, "PP": 0, "GF": 0, "GC": 0, "Pts": 0}
        if not played.empty:
            rel = played[(played["home_team_id"] == tid) | (played["away_team_id"] == tid)]
            for _, m in rel.iterrows():
                hg, ag = safe_int(m["home_goals"], 0), safe_int(m["away_goals"], 0)
                is_home = int(m["home_team_id"]) == tid
                gf, gc = (hg, ag) if is_home else (ag, hg)
                stats["PJ"] += 1
                stats["GF"] += gf
                stats["GC"] += gc
                if gf > gc:
                    stats["PG"] += 1
                    stats["Pts"] += 3
                elif gf == gc:
                    stats["PE"] += 1
                    stats["Pts"] += 1
                else:
                    stats["PP"] += 1
        stats["DG"] = stats["GF"] - stats["GC"]
        stats["Manual"] = 999
        if overrides is not None and not overrides.empty:
            sub = overrides[(overrides["scope"] == "group") & (overrides["team_id"] == tid) & (overrides["group_letter"].fillna("") == group)]
            if not sub.empty:
                stats["Manual"] = safe_int(sub.iloc[0]["manual_order"], 999)
        rows.append(stats)
    df = pd.DataFrame(rows)
    out = []
    for group, g in df.groupby("Grupo", sort=True):
        # Criterios funcionales acordados: puntos, DG, GF, override manual si hiciera falta, orden alfabético.
        # El enfrentamiento directo complejo puede resolverse con ranking_overrides.
        g = g.sort_values(["Pts", "DG", "GF", "Manual", "Equipo"], ascending=[False, False, False, True, True]).copy()
        g["Pos"] = range(1, len(g) + 1)
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def compute_third_place_ranking(standings: pd.DataFrame, overrides: pd.DataFrame | None = None) -> pd.DataFrame:
    if standings.empty:
        return pd.DataFrame()
    thirds = standings[standings["Pos"] == 3].copy()
    if thirds.empty:
        return thirds
    thirds["Manual3"] = 999
    if overrides is not None and not overrides.empty:
        for idx, row in thirds.iterrows():
            sub = overrides[(overrides["scope"] == "third_places") & (overrides["team_id"] == int(row["team_id"]))]
            if not sub.empty:
                thirds.at[idx, "Manual3"] = safe_int(sub.iloc[0]["manual_order"], 999)
    thirds = thirds.sort_values(["Pts", "DG", "GF", "Manual3", "Equipo"], ascending=[False, False, False, True, True]).copy()
    thirds["Rank3"] = range(1, len(thirds) + 1)
    thirds["Clasifica"] = thirds["Rank3"] <= 8
    return thirds


def completed_predictions_count(preds: pd.DataFrame, matches: pd.DataFrame) -> tuple[int, int]:
    total = len(matches)
    if preds.empty:
        return 0, total
    completed = preds.dropna(subset=["predicted_home_goals", "predicted_away_goals"])["match_id"].nunique()
    return int(completed), int(total)


def _rules_pools(rules: dict) -> dict[str, float]:
    """Devuelve los bloques jerárquicos de puntuación.

    La interpretación correcta es:
    - global_*_weight son porcentajes sobre base_points.
    - group_*_weight son porcentajes sobre el bloque de grupos.
    - knockout_*_weight son porcentajes sobre cada ronda de eliminatorias.
    - Los extras se escalan contra el bloque global de extras.
    """
    base_points = rule(rules, "base_points", 1000.0)
    groups_pool = base_points * rule(rules, "global_groups_weight", 40.0) / 100.0
    knockout_pool = base_points * rule(rules, "global_knockout_weight", 50.0) / 100.0
    extras_pool = base_points * rule(rules, "global_extras_weight", 10.0) / 100.0
    return {
        "base_points": base_points,
        "groups_pool": groups_pool,
        "group_positions_pool": groups_pool * rule(rules, "group_positions_weight", 70.0) / 100.0,
        "group_sign_pool": groups_pool * rule(rules, "group_sign_weight", 25.0) / 100.0,
        "group_exact_pool": groups_pool * rule(rules, "group_exact_weight", 5.0) / 100.0,
        "knockout_pool": knockout_pool,
        "extras_pool": extras_pool,
    }


def _predicted_group_matches(group_matches: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    if group_matches.empty:
        return group_matches.copy()
    out = group_matches.copy()
    out["status"] = "pending"
    for idx, m in out.iterrows():
        p = preds[preds["match_id"] == int(m["id"])] if not preds.empty else pd.DataFrame()
        if p.empty:
            continue
        p = p.iloc[0]
        ph = safe_int(p["predicted_home_goals"])
        pa = safe_int(p["predicted_away_goals"])
        if ph is None or pa is None:
            continue
        out.at[idx, "home_goals"] = ph
        out.at[idx, "away_goals"] = pa
        out.at[idx, "status"] = "played"
    return out


def _score_group_positions(group_matches: pd.DataFrame, teams: pd.DataFrame, preds: pd.DataFrame, overrides: pd.DataFrame | None, rules: dict) -> float:
    if group_matches.empty or teams.empty or preds.empty:
        return 0.0

    pools = _rules_pools(rules)
    positions_pool = pools["group_positions_pool"]
    groups = sorted([str(x) for x in teams["group_letter"].dropna().unique().tolist()])
    if not groups:
        return 0.0

    actual = compute_group_standings(group_matches, teams, overrides)
    predicted_matches = _predicted_group_matches(group_matches, preds)
    predicted = compute_group_standings(predicted_matches, teams, None)
    if actual.empty or predicted.empty:
        return 0.0

    points = 0.0
    per_group_pool = positions_pool / len(groups)

    for group in groups:
        real_group_matches = group_matches[group_matches["group_letter"] == group]
        pred_group_matches = predicted_matches[predicted_matches["group_letter"] == group]
        if real_group_matches.empty:
            continue

        # Solo se puntúan posiciones cuando el grupo real está completo y el jugador ha pronosticado todos los partidos del grupo.
        if len(real_group_matches[real_group_matches["status"] == "played"]) < len(real_group_matches):
            continue
        if len(pred_group_matches[pred_group_matches["status"] == "played"]) < len(real_group_matches):
            continue

        actual_g = actual[actual["Grupo"] == group]
        predicted_g = predicted[predicted["Grupo"] == group]
        if actual_g.empty or predicted_g.empty:
            continue

        teams_in_group = max(1, len(actual_g))
        per_exact_position = per_group_pool / teams_in_group
        pred_pos_by_team = {int(r["team_id"]): int(r["Pos"]) for _, r in predicted_g.iterrows()}

        for _, r in actual_g.iterrows():
            team_id = int(r["team_id"])
            if pred_pos_by_team.get(team_id) == int(r["Pos"]):
                points += per_exact_position

    return points


def _score_group_matches(group_matches: pd.DataFrame, preds: pd.DataFrame, rules: dict) -> float:
    if group_matches.empty or preds.empty:
        return 0.0
    pools = _rules_pools(rules)
    sign_pool = pools["group_sign_pool"]
    exact_pool = pools["group_exact_pool"]
    n = max(1, len(group_matches))
    total = 0.0
    for _, m in group_matches.iterrows():
        if m["status"] != "played":
            continue
        p = preds[preds["match_id"] == int(m["id"])]
        if p.empty:
            continue
        p = p.iloc[0]
        rh, ra = safe_int(m["home_goals"]), safe_int(m["away_goals"])
        ph, pa = safe_int(p["predicted_home_goals"]), safe_int(p["predicted_away_goals"])
        if ph is None or pa is None:
            continue
        if match_sign(rh, ra) == match_sign(ph, pa):
            total += sign_pool / n
        if rh == ph and ra == pa:
            total += exact_pool / n
    return total


def _score_groups(group_matches: pd.DataFrame, teams: pd.DataFrame, preds: pd.DataFrame, overrides: pd.DataFrame | None, rules: dict) -> tuple[float, dict[str, float]]:
    positions = _score_group_positions(group_matches, teams, preds, overrides, rules)
    matches_points = _score_group_matches(group_matches, preds, rules)
    total = positions + matches_points
    return total, {"positions": positions, "matches": matches_points}


def _score_knockout(matches: pd.DataFrame, preds: pd.DataFrame, rules: dict) -> float:
    ko = matches[matches["round_key"].isin(KNOCKOUT_ROUNDS)].copy()
    if ko.empty or preds.empty:
        return 0.0
    pools = _rules_pools(rules)
    per_round = pools["knockout_pool"] / len(KNOCKOUT_ROUNDS)
    qualifier_weight = rule(rules, "knockout_qualifier_weight", 70.0) / 100.0
    result_weight = rule(rules, "knockout_result_weight", 30.0) / 100.0
    total = 0.0
    for rk in KNOCKOUT_ROUNDS:
        block = ko[(ko["round_key"] == rk) & (ko["status"] == "played")]
        if block.empty:
            continue
        n = len(block)
        for _, m in block.iterrows():
            p = preds[(preds["match_id"] == int(m["id"])) & (preds["scope"] == rk)]
            if p.empty:
                continue
            p = p.iloc[0]
            if safe_int(p["predicted_winner_team_id"]) == safe_int(m["winner_team_id"]):
                total += per_round * qualifier_weight / n
            rh, ra = safe_int(m["home_goals"]), safe_int(m["away_goals"])
            ph, pa = safe_int(p["predicted_home_goals"]), safe_int(p["predicted_away_goals"])
            if ph is not None and pa is not None and match_sign(rh, ra) == match_sign(ph, pa):
                total += per_round * result_weight / n
    return total


def _score_extras(extra_validations: pd.DataFrame | None, participant_id: int, rules: dict) -> float:
    if extra_validations is None or extra_validations.empty:
        return 0.0
    ok = extra_validations[(extra_validations["participant_id"] == participant_id) & (extra_validations["is_correct"] == 1)]
    if ok.empty:
        return 0.0

    pools = _rules_pools(rules)
    extras_pool = pools["extras_pool"]
    raw_total = sum(rule(rules, key, 0.0) for key in EXTRA_RULE_KEYS)
    if raw_total <= 0:
        return 0.0

    total = 0.0
    for _, r in ok.iterrows():
        key = f"extra_{r['field_key']}"
        total += extras_pool * rule(rules, key, 0.0) / raw_total
    return total


def build_leaderboard(
    participants: pd.DataFrame,
    matches: pd.DataFrame,
    teams: pd.DataFrame,
    predictions: pd.DataFrame,
    overrides: pd.DataFrame,
    rules: dict,
    extra_validations: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    rows = []
    details = {}
    group_matches = matches[matches["round_key"] == "grupos"].copy() if not matches.empty else pd.DataFrame()

    for _, p in participants.iterrows():
        pid = int(p["id"])
        pp = predictions[predictions["participant_id"] == pid] if not predictions.empty else pd.DataFrame()
        initial_preds = pp[pp["scope"] == "initial"] if not pp.empty else pp

        g, group_detail = _score_groups(group_matches, teams, initial_preds, overrides, rules)
        k = _score_knockout(matches, pp, rules)
        e = _score_extras(extra_validations, pid, rules)

        # Los bonus quedan fuera de los 1.000 puntos base. No se aplican aquí porque el modelo actual
        # no guarda todavía una predicción inicial completa del cuadro eliminatorio desde grupos.
        bonus = 0.0
        total = g + k + e + bonus

        rows.append({
            "participant_id": pid,
            "Jugador": p["name"],
            "Total": round(total, 2),
            "Grupos": round(g, 2),
            "Eliminatorias": round(k, 2),
            "Extras": round(e, 2),
            "Bonus": round(bonus, 2),
        })
        details[pid] = {
            "groups_total": g,
            "group_positions": group_detail["positions"],
            "group_matches": group_detail["matches"],
            "knockout_total": k,
            "extras": e,
            "bonus": bonus,
        }

    lb = pd.DataFrame(rows)
    if not lb.empty:
        lb = lb.sort_values(["Total", "Jugador"], ascending=[False, True]).reset_index(drop=True)
        lb.insert(0, "Pos", range(1, len(lb) + 1))
    return lb, details
