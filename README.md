# Site Deployment Kit · Altavia × Idóneos

App web para organizar el despliegue de red en hoteles. Hecha por módulos; hoy están el 1 y el 2.

## Arrancar (Mac / Windows / Linux)

```bash
cd deploykit
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                      # opcional pero recomendado (lectura de etiquetas con IA)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Abre http://localhost:8000. Desde el móvil del técnico (misma WiFi): `http://IP-DEL-PORTATIL:8000`
(la cámara del móvil exige HTTPS salvo en localhost; para producción ver «Siguientes pasos»).

Sin `ANTHROPIC_API_KEY` la app funciona igual, pero lee las etiquetas con Tesseract (OCR local, más flojo y sin leer el
código a rotulador). Para eso hace falta tener instalado el binario `tesseract-ocr`.

## Qué hay

| Módulo | Qué hace |
|---|---|
| Sitio y nombres | Datos del hotel, **plantilla de nombre por tipo de equipo** (tokens `{INN} {MODEL} {FLOOR} {ROOM} {LOC} {ROLE} {N:02} {CODE}`), código corto de modelo, perfiles de cadena reutilizables |
| 1 · Inventario | Sube plantilla (xlsx/csv, incluido el archivo de provisioning de Hilton) · foto de cajas → modelo/MAC/serial/código de caja → pantalla de revisión → validación contra plantilla (coincide / no coincide / fuera de plantilla / repetido) · exporta Excel con las hojas `2a. hardware inventory` y `2b. AP inventory` |
| 2 · Ubicación | Racks (cantidad, MDF/IDF, ubicación) · rack + U de cada equipo con aviso de solapes y vista de rack · AP Plan PDF: detecta marcadores por color, color→modelo, asigna AP01… a cada posición y devuelve el PDF numerado con hoja resumen |

## Ejemplos de nombre ya configurados (perfil Hilton)

* AP: `HLT-{INN}-{MODEL}-{FLOOR}-{ROOM}-{ROLE}{N:02}` → `HLT-PARGE-9172H-2-202-AP23`
* Switch / MX / WLC: `HLT-{INN}-{MODEL}-{LOC}-{ROLE}{N:02}` → `HLT-PARGE-9300-MDF-AS03`

`{N}` sale del número escrito en la caja (SW03 → 3). `{ROLE}` es AP/AS/CS/MX… (en switches se elige AS/CS/DS en Ubicación → B).

## Añadir otra cadena
Sitio y nombres → ajusta las plantillas → «Guardar como perfil de cadena». Al crear un sitio nuevo eliges el perfil.

## Estructura
```
app/main.py        API (FastAPI)            app/naming.py     perfiles + motor de nombres
app/db.py          modelo de datos          app/inventory.py  importar, validar, exportar
app/vision.py      lectura de etiquetas     app/approcess.py  AP Plan: detectar, asignar, anotar PDF
app/static/        interfaz (HTML/JS/CSS)   tests/            plano de prueba y test de flujo completo
data/              base SQLite y PDFs subidos (cambia con DATA_DIR; DATABASE_URL para Postgres)
```
Probar todo el flujo sin interfaz: `python3 tests/test_flow.py`


## Publicación
Ver `DESPLIEGUE.md` (Supabase + Render + subdominio). Variables: `DATABASE_URL`, `ACCESS_CODE`, `ANTHROPIC_API_KEY` (opcional), `TEAM_NAMES` (opcional). Sin `ACCESS_CODE` la app queda abierta (solo para uso local).
Tests: `python3 tests/test_flow.py`, `tests/test_module3.py`, `tests/test_auth.py` (con `DATABASE_URL` apuntando a un Postgres vacío también valen).
