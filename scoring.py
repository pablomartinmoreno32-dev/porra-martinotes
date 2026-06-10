from __future__ import annotations

import pandas as pd

KNOCKOUT_ROUNDS = ["ronda32", "octavos", "cuartos", "semifinales", "final"]


def safe_int(x, default=None):
    try:
        if pd.isna(x):
            return default
        return int(x)
    except Exception:
        return default


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
                stats["PJ"] += 1; stats["GF"] += gf; stats["GC"] += gc
                if gf > gc:
                    stats["PG"] += 1; stats["Pts"] += 3
                elif gf == gc:
                    stats["PE"] += 1; stats["Pts"] += 1
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
        # Simplified agreed criteria: points, GD, GF, manual override, alphabetic.
        # Head-to-head can be overridden manually where necessary.
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


def _score_group_matches(group_matches: pd.DataFrame, preds: pd.DataFrame, rules: dict) -> float:
    if group_matches.empty or preds.empty:
        return 0.0
    base = float(rules["base_points"]) * float(rules["global_groups_weight"]) / 100.0
    match_pool = base * float(rules["group_sign_weight"]) / 100.0
    exact_pool = base * float(rules["group_exact_weight"]) / 100.0
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
            total += match_pool / n
        if rh == ph and ra == pa:
            total += exact_pool / n
    return total


def _score_knockout(matches: pd.DataFrame, preds: pd.DataFrame, rules: dict) -> float:
    ko = matches[matches["round_key"].isin(KNOCKOUT_ROUNDS)].copy()
    if ko.empty or preds.empty:
        return 0.0
    base = float(rules["base_points"]) * float(rules["global_knockout_weight"]) / 100.0
    per_round = base / len(KNOCKOUT_ROUNDS)
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
                total += per_round * float(rules["knockout_qualifier_weight"]) / 100.0 / n
            rh, ra = safe_int(m["home_goals"]), safe_int(m["away_goals"])
            ph, pa = safe_int(p["predicted_home_goals"]), safe_int(p["predicted_away_goals"])
            if ph is not None and pa is not None and match_sign(rh, ra) == match_sign(ph, pa):
                total += per_round * float(rules["knockout_result_weight"]) / 100.0 / n
    return total


def build_leaderboard(participants: pd.DataFrame, matches: pd.DataFrame, teams: pd.DataFrame, predictions: pd.DataFrame, overrides: pd.DataFrame, rules: dict, extra_validations: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    rows = []
    details = {}
    group_matches = matches[matches["round_key"] == "grupos"].copy() if not matches.empty else pd.DataFrame()
    for _, p in participants.iterrows():
        pid = int(p["id"])
        pp = predictions[predictions["participant_id"] == pid] if not predictions.empty else pd.DataFrame()
        g = _score_group_matches(group_matches, pp[pp["scope"] == "initial"] if not pp.empty else pp, rules)
        k = _score_knockout(matches, pp, rules)
        e = 0.0
        if extra_validations is not None and not extra_validations.empty:
            ok = extra_validations[(extra_validations["participant_id"] == pid) & (extra_validations["is_correct"] == 1)]
            for _, r in ok.iterrows():
                key = f"extra_{r['field_key']}"
                e += float(rules.get(key, 0.0))
        bonus = 0.0
        total = g + k + e + bonus
        rows.append({"participant_id": pid, "Jugador": p["name"], "Total": round(total, 2), "Grupos": round(g, 2), "Eliminatorias": round(k, 2), "Extras": round(e, 2), "Bonus": round(bonus, 2)})
        details[pid] = {"groups_total": g, "knockout_total": k, "extras": e, "bonus": bonus}
    lb = pd.DataFrame(rows)
    if not lb.empty:
        lb = lb.sort_values(["Total", "Jugador"], ascending=[False, True]).reset_index(drop=True)
        lb.insert(0, "Pos", range(1, len(lb) + 1))
    return lb, details
