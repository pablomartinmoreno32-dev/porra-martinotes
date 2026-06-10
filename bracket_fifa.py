from __future__ import annotations

from itertools import combinations
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd

# Round-of-32 fixed slots from FIFA regulations.
# Slots that depend on third-place allocation are filled by the Annex C mapping.
ROUND32_FIXED = [
    (73, "2A", "2B"),
    (74, "1E", "3?"),
    (75, "1C", "2F"),
    (76, "1I", "3?"),
    (77, "1A", "3?"),
    (78, "2E", "2I"),
    (79, "1L", "3?"),
    (80, "1D", "3?"),
    (81, "1G", "3?"),
    (82, "1B", "3?"),
    (83, "1H", "2J"),
    (84, "2K", "2L"),
    (85, "1F", "2C"),
    (86, "1J", "2H"),
    (87, "1K", "3?"),
    (88, "2D", "2G"),
]

THIRD_PLACE_MATCHES = {
    "1A": 77,
    "1B": 82,
    "1D": 80,
    "1E": 74,
    "1G": 81,
    "1I": 76,
    "1K": 87,
    "1L": 79,
}

# Column order used by FIFA/Wikipedia Annex C table.
ANNEX_COLUMNS = ["1A", "1B", "1D", "1E", "1G", "1I", "1K", "1L"]

# Candidate pools stated in the official round-of-32 schedule.
CANDIDATE_POOLS = {
    "1E": set("ABCDF"),
    "1I": set("CDFGH"),
    "1A": set("CEFHI"),
    "1L": set("EHIJK"),
    "1D": set("BEFIJ"),
    "1G": set("AEHIJ"),
    "1B": set("EFGIJ"),
    "1K": set("DEIJL"),
}

_WIKI_URL = "https://en.wikipedia.org/wiki/Template:2026_FIFA_World_Cup_third-place_table"


def _normalise_groups(groups: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted({str(g).strip().upper() for g in groups if str(g).strip()}))


def load_annex_c_mapping() -> Dict[Tuple[str, ...], Dict[str, str]]:
    """Load the 495 official FIFA/Wikipedia third-place allocation rows.

    The mapping is intentionally loaded at runtime instead of hard-coding 495 rows.
    Streamlit Cloud normally has outbound internet; if loading fails, the app falls
    back to a deterministic constrained assignment, but the admin is warned.
    """
    tables = pd.read_html(_WIKI_URL)
    table = max(tables, key=lambda x: x.shape[0])
    mapping: Dict[Tuple[str, ...], Dict[str, str]] = {}

    # The table layout can vary slightly. Work with string rows and extract tokens.
    for _, row in table.iterrows():
        tokens: List[str] = []
        for item in row.tolist():
            if pd.isna(item):
                continue
            tokens.extend(str(item).replace("\xa0", " ").split())
        if not tokens or not tokens[0].isdigit():
            continue
        letters = [t for t in tokens[1:] if len(t) == 1 and t.isalpha()]
        thirds = [t[1] for t in tokens[1:] if len(t) == 2 and t.startswith("3") and t[1].isalpha()]
        if len(letters) >= 8 and len(thirds) >= 8:
            key = _normalise_groups(letters[:8])
            mapping[key] = dict(zip(ANNEX_COLUMNS, thirds[:8]))
    if len(mapping) < 495:
        raise RuntimeError(f"Annex C mapping incomplete: {len(mapping)} rows loaded")
    return mapping


def fallback_constrained_mapping(third_groups: Sequence[str]) -> Dict[str, str]:
    """Deterministic fallback when the online Annex C table is unavailable.

    It honours FIFA candidate pools and avoids invalid pairings. It is not a
    replacement for Annex C; it exists only to keep the app usable offline.
    """
    groups = set(_normalise_groups(third_groups))
    slots = sorted(ANNEX_COLUMNS, key=lambda s: len(CANDIDATE_POOLS[s] & groups))
    assigned: Dict[str, str] = {}
    used: set[str] = set()

    def backtrack(i: int) -> bool:
        if i == len(slots):
            return True
        slot = slots[i]
        candidates = sorted((CANDIDATE_POOLS[slot] & groups) - used)
        for g in candidates:
            assigned[slot] = g
            used.add(g)
            if backtrack(i + 1):
                return True
            used.remove(g)
            assigned.pop(slot, None)
        return False

    if not backtrack(0):
        raise ValueError(f"No se pudo construir mapping FIFA para terceros: {sorted(groups)}")
    return assigned


def get_third_place_mapping(third_groups: Sequence[str]) -> tuple[Dict[str, str], bool]:
    key = _normalise_groups(third_groups)
    if len(key) != 8:
        raise ValueError("La ronda de 32 necesita exactamente 8 mejores terceros")
    try:
        mapping = load_annex_c_mapping()[key]
        return mapping, True
    except Exception:
        return fallback_constrained_mapping(key), False


def position_lookup(standings: pd.DataFrame) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for _, row in standings.iterrows():
        out[f"{int(row['Pos'])}{row['Grupo']}"] = int(row["team_id"])
    return out


def build_round32_pairings(standings: pd.DataFrame, thirds: pd.DataFrame) -> tuple[list[dict], bool, Dict[str, str]]:
    """Return official R32 pairings as dictionaries with match_no/team ids."""
    qualified_thirds = thirds[thirds["Clasifica"] == True].copy()
    third_groups = qualified_thirds["Grupo"].tolist()
    mapping, official = get_third_place_mapping(third_groups)
    lookup = position_lookup(standings)
    third_team_by_group = {
        str(r["Grupo"]): int(r["team_id"]) for _, r in qualified_thirds.iterrows()
    }

    rows = []
    for match_no, home_code, away_code in ROUND32_FIXED:
        if away_code == "3?":
            home_group_winner = next(k for k, v in THIRD_PLACE_MATCHES.items() if v == match_no)
            group = mapping[home_group_winner]
            away_id = third_team_by_group[group]
            away_label = f"3{group}"
        else:
            away_id = lookup[away_code]
            away_label = away_code
        rows.append(
            {
                "match_no": match_no,
                "home_code": home_code,
                "away_code": away_label,
                "home_team_id": lookup[home_code],
                "away_team_id": away_id,
            }
        )
    return rows, official, mapping


def next_round_sources(round_key: str) -> list[tuple[int, int, int]]:
    if round_key == "octavos":
        return [(89, 74, 77), (90, 73, 75), (91, 76, 78), (92, 79, 80), (93, 83, 84), (94, 81, 82), (95, 86, 88), (96, 85, 87)]
    if round_key == "cuartos":
        return [(97, 89, 90), (98, 93, 94), (99, 91, 92), (100, 95, 96)]
    if round_key == "semifinales":
        return [(101, 97, 98), (102, 99, 100)]
    if round_key == "final":
        return [(104, 101, 102)]
    return []
