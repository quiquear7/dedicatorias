# Dedicatorias

App para generar tarjetas de dedicatoria personalizadas: graba (o teclea) la dedicatoria, una IA la corrige, eliges una plantilla con tus medidas y obtienes los archivos imprimibles (PDF + PNG a 300 dpi).

## Estructura

```
dedicatorias/
├── app.py                            # Landing
├── pages/
│   ├── 1_Plantillas.py               # CRUD de plantillas
│   ├── 2_Generar_dedicatoria.py      # Flujo de generación (audio / texto)
│   ├── 3_Historial.py                # Historial + duplicación
│   └── 4_Destinatarios.py            # Contactos (nombre + grupo)
├── core/
│   ├── config.py                     # API key + storage
│   ├── storage.py                    # LocalStorage / S3Storage
│   ├── models.py                     # Template, Contact, Dedication
│   ├── templates.py                  # CRUD plantillas
│   ├── contacts.py                   # CRUD contactos
│   ├── history.py                    # CRUD historial
│   ├── transcription.py              # Whisper
│   ├── correction.py                 # GPT-4o-mini
│   └── rendering.py                  # PDF (ReportLab) + PNG (Pillow)
├── data/                             # Sólo en local. Ignorado en git.
├── .env.example
└── .streamlit/secrets.toml.example
```

## Setup local

Requiere Python 3.9+ y `uv` (`brew install uv`).

```bash
uv sync
cp .env.example .env
# Edita .env y rellena UNA de las dos claves (OPENAI_API_KEY o GOOGLE_API_KEY)
uv run streamlit run app.py
```

Los datos (plantillas, contactos, dedicatorias) se guardan en `./data/`.

### Elección de proveedor de IA

| Proveedor | Coste | Modelos usados | Tier gratuito |
|-----------|-------|----------------|---------------|
| **Gemini** (recomendado para empezar) | Gratis dentro del free tier | `gemini-2.5-flash` (audio + corrección) | ~250k tokens/día |
| **OpenAI** | De pago (~$0.20–$0.50 por 100 tarjetas) | `whisper-1` + `gpt-4o-mini` | Casi inexistente |

- **Gemini**: crea una clave gratis en [aistudio.google.com/apikey](https://aistudio.google.com/apikey) y ponla en `GOOGLE_API_KEY`.
- **OpenAI**: añade saldo (mínimo $5) en [platform.openai.com/billing](https://platform.openai.com/settings/organization/billing) y pon la clave en `OPENAI_API_KEY`.
- Si tienes ambas, la variable `AI_PROVIDER=openai|gemini` decide cuál se usa.

### Acceso desde el móvil en la misma WiFi

```bash
uv run streamlit run app.py --server.address=0.0.0.0
```

Luego en el móvil abre `http://<ip-del-mac>:8501` (la IP la ves con `ipconfig getifaddr en0`).

## Despliegue en Streamlit Community Cloud

1. **Cloudflare R2** (almacenamiento persistente; el filesystem de Streamlit Cloud es efímero):
   - Crea cuenta en Cloudflare → R2 → crea un bucket (ej. `dedicatorias-prod`).
   - "Manage R2 API tokens" → crea un token con permisos de lectura/escritura sobre el bucket.
   - Anota: `Account ID`, `Access Key ID`, `Secret Access Key`, y la URL S3 endpoint (`https://<accountid>.r2.cloudflarestorage.com`).

2. **GitHub** (repo privado en tu cuenta personal):
   - Configura git local con tu identidad personal (este Mac tiene la de trabajo en global):
     ```bash
     cd /ruta/al/proyecto
     git init
     git config user.name "<tu nombre>"
     git config user.email "<tu correo personal>"
     git config commit.gpgsign false   # si tienes firma global activada
     ```
   - Crea el repo privado y empuja:
     ```bash
     # Si tienes gh CLI con tu cuenta personal:
     gh auth login                  # elige cuenta personal
     gh repo create dedicatorias --private --source=. --remote=origin --push
     # O bien con HTTPS + Personal Access Token:
     git remote add origin https://github.com/<tu-usuario>/dedicatorias.git
     git add . && git commit -m "Initial commit"
     git push -u origin main
     ```

3. **Streamlit Community Cloud**:
   - Ve a [share.streamlit.io](https://share.streamlit.io), conecta GitHub, elige el repo y `app.py` como entrypoint.
   - En "Advanced settings → Secrets" pega:
     ```toml
     OPENAI_API_KEY = "sk-..."

     STORAGE_BACKEND = "s3"
     S3_BUCKET = "dedicatorias-prod"
     S3_ENDPOINT = "https://<accountid>.r2.cloudflarestorage.com"
     S3_ACCESS_KEY = "..."
     S3_SECRET_KEY = "..."
     S3_REGION = "auto"
     ```
   - Deploy. Te dará una URL `https://<algo>.streamlit.app`.
   - En el móvil: abre la URL en Safari/Chrome → Compartir → "Añadir a pantalla de inicio" para tener un icono que la abre como app.

## Flujo de uso

1. **Destinatarios** — da de alta personas con `nombre + grupo`. Ej: `Enrique – Familia`, `Rodrigo – Amigos Madrid`.
2. **Plantillas** — sube cada diseño (PNG/JPG/PDF), define medidas en mm y la zona donde irá el texto. El preview en vivo te muestra el resultado.
3. **Generar dedicatoria**:
   - Elige destinatario (existente o nuevo).
   - Graba audio (transcripción + corrección por IA) **o** teclea/pega un texto que ya tengas escrito.
   - Revisa y confirma.
   - Selecciona plantilla → exporta PDF + PNG.
4. **Historial** — busca dedicatorias pasadas, descárgalas otra vez o pulsa "Duplicar" sobre una marcada como **genérica** para reutilizar el texto con otro destinatario.

## Notas

- Las dedicatorias quedan persistidas con un *snapshot* de la plantilla usada, así que aunque borres una plantilla, la tarjeta sigue siendo regenerable desde el historial.
- El renderizado del PDF embebe el fondo como imagen a 300 dpi y dibuja el texto vectorialmente encima — tamaño exacto en mm para imprenta.
- Las fuentes del PDF son los Type 1 estándar (Helvetica). Las del PNG dependen de las fuentes del sistema (Helvetica en macOS, DejaVu en Streamlit Cloud Linux). Subir fuentes personalizadas queda para una fase posterior.
