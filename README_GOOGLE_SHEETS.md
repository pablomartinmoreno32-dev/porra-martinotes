# Configuración de Google Sheets

La app funciona localmente con SQLite aunque no haya Google Sheets configurado.

Para activar sincronización con Google Sheets en Streamlit Cloud, añade secretos con esta estructura:

```toml
google_sheet_id = "ID_DE_TU_GOOGLE_SHEET"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

También puedes usar:

```toml
GOOGLE_SHEET_ID = "ID_DE_TU_GOOGLE_SHEET"
```

La cuenta de servicio debe tener permisos de edición sobre el Google Sheet.

## Hojas esperadas

La app crea/sincroniza estas hojas:

- tournaments
- rounds
- participants
- teams
- matches
- predictions
- ranking_overrides
- scoring_rules
- extra_predictions
- extra_validations

## Limpieza antes de compartir la porra

Para una porra limpia, revisa especialmente:

- participants
- predictions
- extra_predictions
- extra_validations
- ranking_overrides

Conserva normalmente:

- tournaments
- rounds
- teams
- matches
- scoring_rules
