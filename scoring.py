from __future__ import annotations

import pandas as pd

KNOCKOUT_ROUNDS = ["ronda32", "octavos", "cuartos", "semifinales", "final"]
BONUS_MILESTONES = {"octavos": "bonus_octavos", "cuartos": "bonus_cuartos", "semifinales": "bonus_semifinales", "final": "bonus_final", "campeon": "bonus_campeon"}
EXTRA_KEYS = ["balon_oro", "bota_oro", "guante_oro", "mejor_joven", "equipo_entretenido", "gol_torneo"]


def _empty_row(team_id: int, team: str, group: str) -> dict:
    return {
        "team_id": team_id,
        "Equipo": team,
        "Grupo": group,
        "Pts": 0,
        "PJ": 0,
        "PG": 0,
        "PE": 0,
        "PP": 0,
        "GF": 0,
        "GC": 0,
        "DG": 0,
    }


def _get_manual_override(overrides_df: pd.DataFrame | None, scope: str, group_letter: str | None, team_id: int) -> int:
    if overrides_df is None or overrides_df.empty:
        return 999
    df = overrides_df[(overrides_df["scope"] == scope) & (overrides_df["team_id"] == team_id)]
    if group_letter is not None:
        df = df[df["group_letter"] == group_letter]
    else:
        df = df[df["group_letter"].isna()]
    if df.empty:
        return 999
    value = df.iloc[0]["manual_order"]
    return int(value) if pd.notna(value) else 999


def _head_to_head_metrics(team_ids: list[int], group_matches: pd.DataFrame) -> dict[int, tuple[int, int, int]]:
    metrics = {tid: {"pts": 0, "gf": 0, "gc": 0} for tid in team_ids}
    relevant = group_matches[
        group_matches["home_team_id"].isin(team_ids)
        & group_matches["away_team_id"].isin(team_ids)
        & (group_matches["status"] == "played")
    ]
    for _, m in relevant.iterrows():
        ht, at = int(m["home_team_id"]), int(m["away_team_id"])
        hg, ag = int(m["home_goals"]), int(m["away_goals"])
        metrics[ht]["gf"] += hg
        metrics[ht]["gc"] += ag
        metrics[at]["gf"] += ag
        metrics[at]["gc"] += hg
        if hg > ag:
            metrics[ht]["pts"] += 3
        elif hg < ag:
            metrics[at]["pts"] += 3
        else:
            metrics[ht]["pts"] += 1
            metrics[at]["pts"] += 1
    return {tid: (v["pts"], v["gf"] - v["gc"], v["gf"]) for tid, v in metrics.items()}


def _rank_group(df: pd.DataFrame, group_matches: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["H2H_Pts"] = 0
    df["H2H_DG"] = 0
    df["H2H_GF"] = 0
    for _, tied in df.groupby("Pts"):
        if len(tied) <= 1:
            continue
        team_ids = [int(x) for x in tied["team_id"].tolist()]
        h2h = _head_to_head_metrics(team_ids, group_matches)
        for tid, values in h2h.items():
            df.loc[df["team_id"] == tid, ["H2H_Pts", "H2H_DG", "H2H_GF"]] = values
    df = df.sort_values(
        by=["Pts", "H2H_Pts", "H2H_DG", "H2H_GF", "DG", "GF", "Manual", "Equipo"],
        ascending=[False, False, False, False, False, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    df.insert(0, "Pos", range(1, len(df) + 1))
    return df


def compute_group_standings(matches_df: pd.DataFrame, teams_df: pd.DataFrame, overrides_df: pd.DataFrame | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    if teams_df.empty:
        return pd.DataFrame()
    if matches_df.empty:
        matches_df = pd.DataFrame(columns=["group_letter", "status", "home_team_id", "away_team_id", "home_goals", "away_goals"])
    for group, group_teams in teams_df.groupby("group_letter", sort=True):
        base = {int(r["id"]): _empty_row(int(r["id"]), str(r["name"]), str(group)) for _, r in group_teams.iterrows()}
        group_matches = matches_df[(matches_df["group_letter"] == group) & (matches_df["status"] == "played")]
        for _, m in group_matches.iterrows():
            ht, at = int(m["home_team_id"]), int(m["away_team_id"])
            if pd.isna(m["home_goals"]) or pd.isna(m["away_goals"]):
                continue
            hg, ag = int(m["home_goals"]), int(m["away_goals"])
            if ht not in base or at not in base:
                continue
            base[ht]["PJ"] += 1
            base[at]["PJ"] += 1
            base[ht]["GF"] += hg
            base[ht]["GC"] += ag
            base[at]["GF"] += ag
            base[at]["GC"] += hg
            if hg > ag:
                base[ht]["Pts"] += 3
                base[ht]["PG"] += 1
                base[at]["PP"] += 1
            elif hg < ag:
                base[at]["Pts"] += 3
                base[at]["PG"] += 1
                base[ht]["PP"] += 1
            else:
                base[ht]["Pts"] += 1
                base[at]["Pts"] += 1
                base[ht]["PE"] += 1
                base[at]["PE"] += 1
        for row in base.values():
            row["DG"] = row["GF"] - row["GC"]
            row["Manual"] = _get_manual_override(overrides_df, "group", group, row["team_id"])
            rows.append(row)
    standings = pd.DataFrame(rows)
    if standings.empty:
        return standings
    ordered = []
    for group, df in standings.groupby("Grupo", sort=True):
        gm = matches_df[matches_df["group_letter"] == group] if not matches_df.empty else pd.DataFrame()
        ordered.append(_rank_group(df, gm))
    return pd.concat(ordered, ignore_index=True)


def compute_third_place_ranking(standings_df: pd.DataFrame, overrides_df: pd.DataFrame | None = None) -> pd.DataFrame:
    if standings_df.empty:
        return standings_df
    thirds = standings_df[standings_df["Pos"] == 3].copy()
    if thirds.empty:
        return thirds
    thirds["Manual_Thirds"] = thirds["team_id"].apply(lambda tid: _get_manual_override(overrides_df, "third_places", None, int(tid)))
    thirds = thirds.sort_values(
        by=["Pts", "DG", "GF", "Manual_Thirds", "Equipo"],
        ascending=[False, False, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    thirds.insert(0, "Rank3", range(1, len(thirds) + 1))
    thirds["Clasifica"] = thirds["Rank3"] <= 8
    return thirds


def completed_predictions_count(predictions_df: pd.DataFrame, matches_df: pd.DataFrame) -> tuple[int, int]:
    total = len(matches_df)
    if total == 0:
        return 0, 0
    completed = set(predictions_df.dropna(subset=["predicted_home_goals", "predicted_away_goals"])["match_id"].astype(int).tolist())
    return len(completed), total


def _sign(a: int, b: int) -> int:
    return 1 if a > b else (-1 if a < b else 0)


def _prediction_matches_as_results(matches_df: pd.DataFrame, predictions_df: pd.DataFrame) -> pd.DataFrame:
    if matches_df.empty or predictions_df.empty:
        return matches_df.iloc[0:0].copy()
    pred = predictions_df.dropna(subset=["predicted_home_goals", "predicted_away_goals"]).copy()
    if pred.empty:
        return matches_df.iloc[0:0].copy()
    merged = matches_df.merge(
        pred[["match_id", "predicted_home_goals", "predicted_away_goals"]],
        left_on="id",
        right_on="match_id",
        how="inner",
    )
    merged["home_goals"] = merged["predicted_home_goals"].astype(int)
    merged["away_goals"] = merged["predicted_away_goals"].astype(int)
    merged["status"] = "played"
    return merged


def _pct(value: float) -> float:
    return value / 100.0


def score_groups_for_participant(
    participant_id: int,
    matches_df: pd.DataFrame,
    teams_df: pd.DataFrame,
    predictions_df: pd.DataFrame,
    overrides_df: pd.DataFrame,
    rules: dict[str, float],
) -> dict:
    base = float(rules.get("base_points", 1000))
    groups_max = base * _pct(rules.get("global_groups_weight", 40))
    positions_max = groups_max * _pct(rules.get("group_positions_weight", 70))
    sign_max = groups_max * _pct(rules.get("group_sign_weight", 25))
    exact_max = groups_max * _pct(rules.get("group_exact_weight", 5))

    group_matches = matches_df[matches_df["round_key"] == "grupos"].copy()
    real_played = group_matches[group_matches["status"] == "played"].copy()
    preds = predictions_df[
        (predictions_df["participant_id"] == participant_id) & (predictions_df["scope"] == "initial")
    ].copy()

    total_group_matches = max(len(group_matches), 1)
    total_positions = max(len(teams_df), 1)

    sign_ok = 0
    exact_ok = 0
    evaluated = 0
    if not real_played.empty and not preds.empty:
        merged = real_played.merge(preds, left_on="id", right_on="match_id", how="left", suffixes=("", "_pred"))
        for _, r in merged.iterrows():
            if pd.isna(r.get("predicted_home_goals")) or pd.isna(r.get("predicted_away_goals")):
                continue
            evaluated += 1
            rh, ra = int(r["home_goals"]), int(r["away_goals"])
            ph, pa = int(r["predicted_home_goals"]), int(r["predicted_away_goals"])
            if _sign(rh, ra) == _sign(ph, pa):
                sign_ok += 1
            if rh == ph and ra == pa:
                exact_ok += 1

    # IMPORTANT: scores are normalized against the full universe of group-stage
    # opportunities, not just against matches already played. Otherwise a single
    # correct result would incorrectly award the full signs/exact-results block.
    sign_score = sign_max * sign_ok / total_group_matches
    exact_score = exact_max * exact_ok / total_group_matches

    # Position scoring is only evaluated for groups whose real matches are fully
    # completed. Incomplete/provisional standings must not award points.
    complete_groups: set[str] = set()
    if not group_matches.empty:
        for group, gm in group_matches.groupby("group_letter"):
            total_in_group = len(gm)
            played_in_group = len(gm[gm["status"] == "played"])
            if total_in_group > 0 and played_in_group == total_in_group:
                complete_groups.add(str(group))

    real_standings = compute_group_standings(group_matches, teams_df, overrides_df)
    pred_matches = _prediction_matches_as_results(group_matches, preds)
    pred_standings = compute_group_standings(pred_matches, teams_df, overrides_df)

    pos_ok = 0
    positions_evaluated = 0
    completed_groups_count = 0
    if complete_groups and not real_standings.empty and not pred_standings.empty:
        real_pos = {(int(r["team_id"]), str(r["Grupo"])): int(r["Pos"]) for _, r in real_standings.iterrows()}
        pred_pos = {(int(r["team_id"]), str(r["Grupo"])): int(r["Pos"]) for _, r in pred_standings.iterrows()}
        pred_match_ids = set(preds.dropna(subset=["predicted_home_goals", "predicted_away_goals"])["match_id"].astype(int).tolist())
        for group in sorted(complete_groups):
            gm = group_matches[group_matches["group_letter"] == group]
            # If the player did not fill the whole group, do not award position
            # points for that group.
            if not set(gm["id"].astype(int).tolist()).issubset(pred_match_ids):
                continue
            completed_groups_count += 1
            group_team_ids = teams_df[teams_df["group_letter"] == group]["id"].astype(int).tolist()
            positions_evaluated += len(group_team_ids)
            for tid in group_team_ids:
                key = (tid, group)
                if pred_pos.get(key) == real_pos.get(key):
                    pos_ok += 1

    positions_score = positions_max * pos_ok / total_positions
    return {
        "groups_total": positions_score + sign_score + exact_score,
        "groups_positions": positions_score,
        "groups_signs": sign_score,
        "groups_exact": exact_score,
        "positions_ok": pos_ok,
        "positions_total": total_positions,
        "positions_evaluated": positions_evaluated,
        "completed_groups_count": completed_groups_count,
        "signs_ok": sign_ok,
        "signs_total": total_group_matches,
        "signs_evaluated": evaluated,
        "exact_ok": exact_ok,
        "exact_total": total_group_matches,
        "exact_evaluated": evaluated,
    }


def score_knockouts_for_participant(
    participant_id: int,
    matches_df: pd.DataFrame,
    predictions_df: pd.DataFrame,
    rules: dict[str, float],
) -> dict:
    base = float(rules.get("base_points", 1000))
    knock_max = base * _pct(rules.get("global_knockout_weight", 50))
    round_max = knock_max / len(KNOCKOUT_ROUNDS)
    qualifier_weight = _pct(rules.get("knockout_qualifier_weight", 70))
    result_weight = _pct(rules.get("knockout_result_weight", 30))
    et_mult = float(rules.get("extra_time_multiplier", 0.5))
    pen_mult = float(rules.get("penalties_multiplier", 0.5))

    preds_all = predictions_df[predictions_df["participant_id"] == participant_id].copy()
    total = 0.0
    qualifier_score = 0.0
    result_score = 0.0
    detail_rows = []
    for rk in KNOCKOUT_ROUNDS:
        round_matches = matches_df[(matches_df["round_key"] == rk) & (matches_df["status"] == "played")].copy()
        if round_matches.empty:
            continue
        q_per_match = (round_max * qualifier_weight) / len(round_matches)
        r_per_match = (round_max * result_weight) / len(round_matches)
        for _, m in round_matches.iterrows():
            pred = preds_all[(preds_all["match_id"] == int(m["id"])) & (preds_all["scope"] == rk)]
            if pred.empty:
                detail_rows.append({"round": rk, "match_id": int(m["id"]), "qualifier": 0, "result": 0})
                continue
            p = pred.iloc[0]
            q = 0.0
            rscore_raw = 0.0
            # El pase se puntúa contra el equipo que pasa. Si no hay penaltis se habrá deducido del marcador.
            if pd.notna(p.get("predicted_winner_team_id")) and pd.notna(m.get("winner_team_id")) and int(p["predicted_winner_team_id"]) == int(m["winner_team_id"]):
                q = q_per_match
            if int(m.get("penalties") or 0) == 0 and pd.notna(p.get("predicted_home_goals")) and pd.notna(p.get("predicted_away_goals")):
                if int(p["predicted_home_goals"]) == int(m["home_goals"]) and int(p["predicted_away_goals"]) == int(m["away_goals"]):
                    rscore_raw += 1.0
            # Si el partido va a penaltis, el marcador queda subordinado a haber acertado penaltis + pase.
            # Solo se premia acertar prórroga/penaltis cuando realmente ocurrieron.
            denom = 1.0
            if int(m.get("extra_time") or 0) == 1:
                denom += et_mult
                if int(p.get("predicted_extra_time") or 0) == 1:
                    rscore_raw += et_mult
            if int(m.get("penalties") or 0) == 1:
                denom += pen_mult
                if int(p.get("predicted_penalties") or 0) == 1:
                    rscore_raw += pen_mult
            rscore = r_per_match * (rscore_raw / denom)
            qualifier_score += q
            result_score += rscore
            total += q + rscore
            detail_rows.append({"round": rk, "match_id": int(m["id"]), "qualifier": q, "result": rscore})
    return {
        "knockout_total": total,
        "knockout_qualifier": qualifier_score,
        "knockout_result": result_score,
        "knockout_details": detail_rows,
    }


def _actual_milestones_from_matches(matches_df: pd.DataFrame) -> dict[str, set[int]]:
    milestones = {"octavos": set(), "cuartos": set(), "semifinales": set(), "final": set(), "campeon": set()}
    round_to_milestone = {"ronda32": "octavos", "octavos": "cuartos", "cuartos": "semifinales", "semifinales": "final", "final": "campeon"}
    for rk, milestone in round_to_milestone.items():
        df = matches_df[(matches_df["round_key"] == rk) & (matches_df["status"] == "played")].copy()
        for _, m in df.iterrows():
            if pd.notna(m.get("winner_team_id")):
                milestones[milestone].add(int(m["winner_team_id"]))
    return milestones


def _initial_milestones_from_bracket(bracket_df: pd.DataFrame, participant_id: int) -> dict[str, set[int]]:
    milestones = {"octavos": set(), "cuartos": set(), "semifinales": set(), "final": set(), "campeon": set()}
    if bracket_df is None or bracket_df.empty:
        return milestones
    df = bracket_df[(bracket_df["participant_id"] == participant_id) & (bracket_df["scope"] == "initial")].copy()
    round_to_milestone = {"ronda32": "octavos", "octavos": "cuartos", "cuartos": "semifinales", "semifinales": "final", "final": "campeon"}
    for rk, milestone in round_to_milestone.items():
        rdf = df[df["round_key"] == rk]
        for _, r in rdf.iterrows():
            if pd.notna(r.get("predicted_winner_team_id")):
                milestones[milestone].add(int(r["predicted_winner_team_id"]))
    return milestones


def score_initial_bonus_for_participant(participant_id: int, matches_df: pd.DataFrame, bracket_df: pd.DataFrame, rules: dict[str, float]) -> dict:
    actual = _actual_milestones_from_matches(matches_df)
    predicted = _initial_milestones_from_bracket(bracket_df, participant_id)
    total = 0.0
    detail = {}
    for milestone, rule_key in BONUS_MILESTONES.items():
        hits = predicted[milestone] & actual[milestone]
        pts = len(hits) * float(rules.get(rule_key, 0.0))
        detail[milestone] = {"hits": len(hits), "points": pts, "team_ids": sorted(hits)}
        total += pts
    return {"bonus": total, "bonus_detail": detail}


def score_extras_for_participant(participant_id: int, validations_df: pd.DataFrame, rules: dict[str, float]) -> dict:
    total = 0.0
    detail = {}
    if validations_df is None or validations_df.empty:
        return {"extras": 0.0, "extras_detail": detail}
    df = validations_df[validations_df["participant_id"] == participant_id]
    for key in EXTRA_KEYS:
        ok = not df[(df["extra_key"] == key) & (df["is_correct"].astype(int) == 1)].empty
        pts = float(rules.get(f"extra_{key}", 0.0)) if ok else 0.0
        detail[key] = {"correct": ok, "points": pts}
        total += pts
    return {"extras": total, "extras_detail": detail}


def build_leaderboard(
    participants_df: pd.DataFrame,
    matches_df: pd.DataFrame,
    teams_df: pd.DataFrame,
    predictions_df: pd.DataFrame,
    overrides_df: pd.DataFrame,
    rules: dict[str, float],
    bracket_df: pd.DataFrame | None = None,
    extra_validations_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[int, dict]]:
    rows = []
    details: dict[int, dict] = {}
    if bracket_df is None:
        bracket_df = pd.DataFrame()
    if extra_validations_df is None:
        extra_validations_df = pd.DataFrame()
    for _, p in participants_df.iterrows():
        pid = int(p["id"])
        gs = score_groups_for_participant(pid, matches_df, teams_df, predictions_df, overrides_df, rules)
        ks = score_knockouts_for_participant(pid, matches_df, predictions_df, rules)
        es = score_extras_for_participant(pid, extra_validations_df, rules)
        bs = score_initial_bonus_for_participant(pid, matches_df, bracket_df, rules)
        total = gs["groups_total"] + ks["knockout_total"] + es["extras"] + bs["bonus"]
        details[pid] = {**gs, **ks, **es, **bs, "total": total}
        rows.append({
            "Jugador": p["name"],
            "participant_id": pid,
            "Total": round(total, 2),
            "Grupos": round(gs["groups_total"], 2),
            "Eliminatorias": round(ks["knockout_total"], 2),
            "Extras": round(es["extras"], 2),
            "Bonus": round(bs["bonus"], 2),
        })
    lb = pd.DataFrame(rows)
    if not lb.empty:
        lb = lb.sort_values(["Total", "Jugador"], ascending=[False, True]).reset_index(drop=True)
        lb.insert(0, "Pos", range(1, len(lb) + 1))
    return lb, details
