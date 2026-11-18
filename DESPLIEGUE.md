# Publicar la app en parge.grupoaltavia.com

Cómo encaja todo:

| Pieza | Para qué | Dónde |
|---|---|---|
| **Supabase** | Base de datos (inventario, avances, fotos, plano) | supabase.com |
| **GitHub** | Guardar el código para que Render lo despliegue | github.com |
| **Render** | Ejecutar la app (Python) 24 h, con HTTPS | render.com |
| **Hostinger (DNS)** | Apuntar `parge.grupoaltavia.com` a Render | hPanel |
| **WordPress** | **No se toca.** Sigue en grupoaltavia.com como hasta ahora | — |

> Por qué no se publica «dentro» de WordPress/Hostinger: el alojamiento de WordPress ejecuta PHP, no aplicaciones Python. La app vive en Render y el subdominio simplemente apunta a ella. Tu web no se ve afectada.

Tiempo estimado: 40–60 minutos la primera vez.

---

## 1. Supabase (la base de datos)

1. Entra en **supabase.com** → *Start your project* → inicia sesión (con GitHub o email).
2. **New project**:
   - *Name*: `parge-deployment`
   - *Database password*: pulsa *Generate* y **cópiala ya a un sitio seguro** (solo se muestra una vez).
   - *Region*: una de Europa (París o Fráncfort).
   - Plan: el gratuito vale para empezar. Ojo: el plan gratuito **pausa** el proyecto tras una semana sin actividad; durante la instalación no pasará, pero compruébalo en los planes vigentes.
3. Espera 1–2 minutos a que se cree.
4. Arriba, pulsa **Connect** → pestaña **Connection string** (o *ORMs*) → elige **Session pooler** (puerto 5432).
   - Usa el *Session pooler* y no *Direct connection*: la conexión directa es solo IPv6 y Render no puede usarla.
   - Verás algo así: `postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-eu-west-3.pooler.supabase.com:5432/postgres`
5. Sustituye `[YOUR-PASSWORD]` por tu contraseña. Si tiene símbolos, codifícalos: `@` → `%40`, `#` → `%23`, `/` → `%2F`, `:` → `%3A`. (Más fácil: genera una contraseña solo con letras y números.)
6. Guarda esa cadena completa: será la variable `DATABASE_URL`.

**No hay que crear tablas.** La app las crea sola la primera vez que arranca, y además activa la protección de filas (RLS) para que la API pública de Supabase no pueda leerlas.

---

## 2. GitHub (el código)

1. En **github.com** → *New repository* → nombre `parge-deployment` → **Private** → *Create*. (Privado: dentro hay el plano y el pedido del hotel de ejemplo.)
2. En tu ordenador, descomprime `site-deployment-kit.zip`, abre una terminal en la carpeta `deploykit` y ejecuta:

```bash
git init
git add .
git commit -m "Site Deployment Kit"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/parge-deployment.git
git push -u origin main
```

(Sin terminal: en el repositorio vacío, *uploading an existing file* y arrastra el contenido de la carpeta `deploykit`, sin la carpeta `data`.)

---

## 3. Render (donde corre la app)

1. **render.com** → crea cuenta → **New +** → **Web Service** → conecta tu GitHub → elige `parge-deployment`.
2. Ajustes:
   - *Language*: **Docker** (viene el `Dockerfile` en el proyecto)
   - *Region*: Frankfurt (la más cercana a España)
   - *Instance type*: un plan **de pago de entrada**. El gratuito se «duerme» a los 15 minutos y el técnico esperaría ~1 minuto cada vez que abra la app. Consulta el precio vigente en render.com/pricing.
3. **Environment Variables** (*Add Environment Variable*):

| Variable | Valor |
|---|---|
| `DATABASE_URL` | la cadena de Supabase del paso 1 |
| `ACCESS_CODE` | el código único que daréis al equipo (p. ej. `parge-2026`) |
| `ANTHROPIC_API_KEY` | *(opcional)* clave de console.anthropic.com para leer las etiquetas de las cajas con IA. Sin ella usa OCR local, bastante menos preciso |

4. En *Advanced* → **Health Check Path**: `/api/health`.
5. **Create Web Service**. La primera construcción tarda unos minutos. Cuando acabe verás una URL tipo `parge-deployment.onrender.com`: ábrela, debe pedirte el código.

---

## 4. El dominio parge.grupoaltavia.com

1. En Render → tu servicio → **Settings** → **Custom Domains** → *Add Custom Domain* → escribe `parge.grupoaltavia.com`. Render te muestra el destino del CNAME (algo como `parge-deployment.onrender.com`).
2. En **Hostinger** → hPanel → **Dominios** → `grupoaltavia.com` → **DNS / Servidores de nombres** → *Registros DNS* → añadir registro:
   - *Tipo*: **CNAME**
   - *Nombre*: `parge`
   - *Apunta a*: el destino que te dio Render
   - *TTL*: el que viene por defecto
3. **Si ya existía** un registro llamado `parge` (A o CNAME) o un subdominio `parge` creado en la sección de Sitios web, bórralo antes: no pueden coexistir.
4. Vuelve a Render y pulsa **Verify**. En unos minutos (a veces hasta una hora) emite el certificado HTTPS automáticamente.
5. Prueba `https://parge.grupoaltavia.com`.

> Si los servidores de nombres de grupoaltavia.com no son los de Hostinger (p. ej. Cloudflare), el registro se añade allí, no en hPanel.

---

## 5. Primer uso

1. Abre la web, escribe tu nombre y el código.
2. **Sitio y nombres** → sube el logo del hotel (el oficial que te dé el hotel o Hilton).
3. **1 · Inventario** → sube el Hardware Order → numera las cajas (AP, luego switches) → baja la hoja de etiquetas.
4. **2 · Ubicación** → AP Plan (PDF) → asignar números.
5. **3 · Instalación** → los técnicos marcan cada equipo desde el móvil.
6. Pasa a Héctor, César, Luis, Rubén y Otto la dirección y el código. En el móvil: menú del navegador → *Añadir a pantalla de inicio*, y queda como una app.
7. El PM (y tú) abrís la misma dirección desde el ordenador: la pantalla de **3 · Instalación** se refresca sola cada 25 segundos.

---

## Mantenimiento

- **Cambiar el código de acceso**: en Render → Environment → `ACCESS_CODE` → guardar. Todo el mundo tendrá que volver a entrar.
- **Actualizar la app**: sube los cambios a GitHub (`git push`) y Render redespliega solo.
- **Copia de seguridad**: todo está en Supabase. Antes de empezar la instalación conviene descargar una copia desde *Database → Backups* (según el plan) o con `pg_dump` y la misma cadena de conexión.
- **Seguridad**: el acceso es un único código compartido, pensado para un uso interno sin datos sensibles. Cualquiera con el código puede modificar datos, y no hay registro por usuario salvo el nombre que cada uno escribe al entrar.
- **Si algo falla**: Render → *Logs*. Un `503` o pantalla en blanco suele ser un `DATABASE_URL` mal copiado (contraseña con símbolos sin codificar, o cadena *Direct* en lugar de *Session pooler*).

## Variante: Hostinger VPS

Solo si ya tienes un VPS de Hostinger: instala Docker, construye la imagen con el `Dockerfile` y ejecútala con las mismas variables de entorno detrás de un proxy con HTTPS (Caddy es lo más sencillo), y en el DNS crea un registro **A** `parge` → IP del VPS en lugar del CNAME.
