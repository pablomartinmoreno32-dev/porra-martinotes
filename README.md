# Porra Martinotes - versión conversación completa

App Streamlit/Python para gestionar una porra del Mundial.

## Ejecutar en local

```cmd
cd /d "C:\Users\USER\Downloads\Personal\Temas de Estudio\App Mundial\porra_martinotes_I"
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
streamlit run app.py
```

Después abre: http://localhost:8501

## Accesos demo

- Código torneo: `MARTINOTES`
- PIN admin: `9999`

## Funcionalidad incluida

- Acceso jugador con nombre + PIN personal.
- Acceso admin separado.
- Rondas con cierre configurable.
- Partidos/resultados con filtros.
- Crear, editar y eliminar partidos.
- Guardar todos los resultados visibles.
- 0-0 válido solo cuando el resultado está confirmado.
- Clasificación fase de grupos.
- Predicciones iniciales por jornada.
- Bracket inicial automático desde predicciones de grupos.
- Predicciones estándar por ronda.
- Penaltis: si se marca penaltis, se oculta el marcador y obliga a elegir quién pasa.
- Ronda de 32 automática desde la clasificación real, con criterio simplificado acordado.
- Bracket admin básico.
- Reglas de puntuación editables.
- Clasificación general.
- Bonus por predicción inicial: octavos +1, cuartos +3, semis +6, final +12, campeón +25.
- Extras: Balón de Oro 25; Bota de Oro, Guante de Oro, Mejor Joven, Equipo más entretenido y Gol del Torneo 15 cada uno.
- Extras con texto libre y validación manual del admin.

## Nota sobre bracket

La generación automática usa los criterios simplificados acordados en conversación: clasificación de grupo por puntos + enfrentamiento directo + DG + GF + orden alfabético; mejores terceros por puntos + DG + GF + orden alfabético. La asignación de cruces es determinista y testeable, no la matriz oficial FIFA de 495 combinaciones.

## Modo Google Sheets

Ver `README_GOOGLE_SHEETS.md`.
