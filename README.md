# Porra Martinotes

Aplicación Streamlit para gestionar una porra del Mundial 2026 entre amigos.

## Qué incluye esta versión

- Login por código de torneo, nombre y PIN.
- Perfil jugador y perfil administrador.
- Predicciones de fase de grupos.
- Predicciones de eliminatorias por ronda.
- Predicciones extra.
- Vista de predicciones de compañeros en modo solo lectura.
- Gestión de partidos y resultados oficiales por administrador.
- Generación de ronda de 32 con lógica FIFA para mejores terceros.
- Generación de rondas posteriores a partir de ganadores reales.
- Clasificación deportiva de grupos.
- Clasificación general de la porra.
- SQLite local como caché/base operativa.
- Sincronización opcional con Google Sheets.

## Ejecución local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

En macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Archivos principales

- `app.py`: interfaz Streamlit y flujo de jugador/admin.
- `database.py`: persistencia SQLite y sincronización opcional con Google Sheets.
- `scoring.py`: clasificación deportiva y ranking de la porra.
- `bracket_fifa.py`: generación de cruces de ronda de 32 según mejores terceros.
- `seed_data.py`: equipos, grupos, rondas, extras y reglas iniciales.

## Credenciales

No subas `service_account.json` a GitHub. Usa secretos de Streamlit Cloud.

## Despliegue en Streamlit Cloud

1. Sube estos archivos al repositorio GitHub.
2. En Streamlit Cloud, apunta a `app.py`.
3. Configura los secretos de Google Sheets si quieres persistencia en Sheets.
4. Reinicia la app.
