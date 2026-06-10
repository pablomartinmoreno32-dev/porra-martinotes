DEFAULT_TOURNAMENT_CODE = "MARTINOTES"
DEFAULT_ADMIN_PIN = "9999"

GROUPS = {
    "A": ["MEX", "RSA", "KOR", "CZE"],
    "B": ["CAN", "BIH", "QAT", "SUI"],
    "C": ["BRA", "MAR", "HAI", "SCO"],
    "D": ["USA", "PAR", "AUS", "TUR"],
    "E": ["GER", "CUW", "CIV", "ECU"],
    "F": ["NED", "JPN", "SWE", "TUN"],
    "G": ["BEL", "EGY", "IRN", "NZE"],
    "H": ["ESP", "CPV", "KSA", "URU"],
    "I": ["FRA", "SEN", "IRQ", "NOR"],
    "J": ["ARG", "ALG", "AUT", "JOR"],
    "K": ["POR", "COD", "UZB", "COL"],
    "L": ["ENG", "CRO", "GHA", "PAN"],
}

ROUND_KEYS = ["grupos", "ronda32", "octavos", "cuartos", "semifinales", "final"]
ROUND_NAMES = {
    "grupos": "Fase de grupos",
    "ronda32": "Ronda de 32",
    "octavos": "Octavos",
    "cuartos": "Cuartos",
    "semifinales": "Semifinales",
    "final": "Final",
}

DEFAULT_RULES = {
    "base_points": 1000,
    "global_groups_weight": 40,
    "global_knockout_weight": 50,
    "global_extras_weight": 10,
    "group_positions_weight": 70,
    "group_sign_weight": 25,
    "group_exact_weight": 5,
    "knockout_qualifier_weight": 70,
    "knockout_result_weight": 30,
    "bonus_octavos": 1,
    "bonus_cuartos": 3,
    "bonus_semifinales": 6,
    "bonus_final": 12,
    "bonus_campeon": 25,
    "extra_balon_oro": 25,
    "extra_bota_oro": 15,
    "extra_guante_oro": 15,
    "extra_mejor_joven": 15,
    "extra_equipo_entretenido": 15,
    "extra_gol_torneo": 15,
}

EXTRA_FIELDS = {
    "campeon": "Campeón",
    "balon_oro": "Balón de Oro",
    "bota_oro": "Bota de Oro",
    "guante_oro": "Guante de Oro",
    "mejor_joven": "Mejor joven",
    "equipo_entretenido": "Equipo más entretenido",
    "gol_torneo": "Gol del torneo",
}
