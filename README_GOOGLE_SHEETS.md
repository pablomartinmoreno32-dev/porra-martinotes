# Porra Martinotes — modo Google Sheets

Esta versión mantiene la lógica de Streamlit igual y cambia la persistencia a Google Sheets cuando encuentra credenciales.

## 1. Nombre del Google Sheet

La app espera por defecto un Google Sheet llamado:

```text
porra_martinotes_db
```

Si quieres usar otro nombre, define `GOOGLE_SHEET_NAME`.

## 2. Prueba local con JSON

Coloca el JSON descargado de Google Cloud dentro de la carpeta del proyecto con este nombre exacto:

```text
service_account.json
```

Después ejecuta:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Si el JSON existe y el Sheet está compartido con la service account como Editor, la app usará Google Sheets.

Si no existe `service_account.json`, la app vuelve a SQLite local.

## 3. Streamlit Cloud — Secrets

En Streamlit Cloud no subas el JSON al repositorio. Pega su contenido en:

```text
App → Settings → Secrets
```

Formato:

```toml
GOOGLE_SHEET_NAME = "porra_martinotes_db"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "porra-martinotes@porra-martinotes.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

## 4. Pestañas creadas automáticamente

La app crea y actualiza estas pestañas en Google Sheets:

- tournaments
- rounds
- participants
- teams
- matches
- predictions
- ranking_overrides
- scoring_rules
- bracket_predictions
- extra_predictions
- extra_validations

No edites manualmente las cabeceras de esas pestañas.

## 5. Regla práctica

Google Sheets se usa como base de datos. No hace falta meter fórmulas en Sheets.
