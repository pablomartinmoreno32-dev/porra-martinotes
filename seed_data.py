"""Seed data for Porra Martinotes.

The groups below are a working draft taken from the user's planning notes.
Review against the official FIFA fixture before production use.
"""

DEFAULT_TOURNAMENT_CODE = "PORRA_MARTINOTES"
DEFAULT_ADMIN_PIN = "9999"

GROUPS = {
    "A": ["México", "Sudáfrica", "Corea del Sur", "Chequia"],
    "B": ["Canadá", "Bosnia y Herzegovina", "Catar", "Suiza"],
    "C": ["Brasil", "Marruecos", "Haití", "Escocia"],
    "D": ["Estados Unidos", "Paraguay", "Australia", "Turquía"],
    "E": ["Alemania", "Curazao", "Costa de Marfil", "Ecuador"],
    "F": ["Países Bajos", "Japón", "Suecia", "Túnez"],
    "G": ["Bélgica", "Egipto", "Irán", "Nueva Zelanda"],
    "H": ["España", "Cabo Verde", "Arabia Saudita", "Uruguay"],
    "I": ["Francia", "Senegal", "Irak", "Noruega"],
    "J": ["Argentina", "Argelia", "Austria", "Jordania"],
    "K": ["Portugal", "RD Congo", "Uzbekistán", "Colombia"],
    "L": ["Inglaterra", "Croacia", "Ghana", "Panamá"],
}

# Generic 4-team group schedule: each team plays all others once.
# Jornada 1: 1v2, 3v4; Jornada 2: 1v3, 2v4; Jornada 3: 1v4, 2v3.
GENERIC_GROUP_PAIRINGS = [
    (1, 0, 1),
    (1, 2, 3),
    (2, 0, 2),
    (2, 1, 3),
    (3, 0, 3),
    (3, 1, 2),
]

DEFAULT_ROUNDS = [
    ("grupos", "Fase de grupos", "open", "2026-06-11 18:00"),
    ("ronda32", "Ronda de 32", "pending", "2026-06-28 16:00"),
    ("octavos", "Octavos", "pending", "2026-07-04 16:00"),
    ("cuartos", "Cuartos", "pending", "2026-07-09 16:00"),
    ("semifinales", "Semifinales", "pending", "2026-07-14 16:00"),
    ("final", "Final", "pending", "2026-07-19 16:00"),
]
