# Tomo

Aplicación web para lectores de manga. A partir de tu exportación de
MyAnimeList, te ayuda a descubrir qué leer, saber cuáles de tus mangas salen en
papel en Argentina, y llevar el control de los tomos que tenés en tu estantería.

Funciona con cuentas de usuario: cada persona tiene su propia lista de
MyAnimeList, su biblioteca y sus correcciones, mientras que el catálogo de
Ivrea se comparte entre todos. Hoy corre en tu computadora con una base de datos
PostgreSQL local; el despliegue online está en la hoja de ruta (abajo).

## Qué hace

**Descubrir**
- Busca mangas en publicación y los agrupa por revista. Las revistas se detectan
  solas a partir de los mangas que estás leyendo en tu lista.
- Filtros por género y tema, con contadores globales sin repetidos.
- Caché en disco: la primera búsqueda tarda, las siguientes son casi inmediatas.

**En Argentina**
- Cruza tu lista de MyAnimeList con el catálogo de Ivrea Argentina y con las
  listas de MyAnimeList de mangas publicados en el país.
- Muestra editorial, cantidad de tomos y estado de la edición argentina.
- Indica cuál es el **próximo tomo**, las **reediciones** y las ediciones
  especiales que anuncia el calendario de salidas, con fecha y número.
- Permite corregir datos o cargar un manga a mano; lo cargado a mano tiene
  prioridad sobre las fuentes automáticas.

**Mi biblioteca**
- Marcás qué tomos tenés en papel (un toque: lo tengo, dos: edición especial).
- El total de tomos se actualiza solo cuando sale uno nuevo.

## Cómo está hecho

- **Backend:** Python con Flask. Las búsquedas largas corren en hilos de
  fondo y el navegador consulta el avance (polling).
- **Frontend:** HTML, CSS y JavaScript sin frameworks, en modo oscuro.
- **Datos:** PostgreSQL con migraciones SQL versionadas (`migrations/`) y un
  pool de conexiones (psycopg 3). Los datos personales (biblioteca, correcciones
  manuales, exportación de MAL) tienen tablas propias; los catálogos externos
  (Ivrea, calendario) se guardan como documentos JSONB y se escriben solo
  cuando cambian. Todo el SQL está en `store.py`.
- **Cuentas:** registro e inicio de sesión con email y contraseña (hash scrypt),
  sesión en una cookie firmada `HttpOnly` y `SameSite=Lax`, límite de intentos
  fallidos, rechazo de escrituras con `Origin` ajeno y borrado de cuenta con
  todos sus datos. Todo lo personal lleva `user_id`; cada usuario solo ve y
  modifica lo suyo (`auth.py`).
- **Tareas compartidas:** el catálogo de Ivrea lo actualiza una sola tarea para
  todos, con un candado de PostgreSQL para que nunca corran dos a la vez (ni
  desde procesos distintos). El avance se guarda en la base y los demás
  procesos recargan el catálogo cuando cambia. Se lanza desde la app, desde un
  programador interno o por línea de comandos (`tomo/jobs.py`).
- **Archivos subidos:** la exportación de MAL se lee con `defusedxml` y con un
  tope de tamaño al descomprimir, contra XML malicioso y bombas de compresión.
- **Respeto a las fuentes:** límite de pedidos a la API y pausas entre pedidos
  al scrapear.
- **Scraping:** lector del sitio de Ivrea con normalización de títulos para
  emparejar series entre fuentes distintas y resolver ambigüedades.

## Estructura del proyecto

```
Tomo/
├── app.py                 Punto de entrada: python app.py
├── INICIAR.bat            Arranque en Windows (Docker + entorno + app)
├── requirements.txt       Dependencias de la app
├── requirements-dev.txt   Dependencias para desarrollo (agrega pytest)
├── pytest.ini             Configuración de las pruebas
├── docker-compose.yml     PostgreSQL local para desarrollo
├── wsgi.py                Entrada de producción (gunicorn)
├── gunicorn.conf.py       Configuración del servidor de producción
├── Dockerfile             Imagen de producción
├── render.yaml            Despliegue en Render
├── .github/workflows/     tests.yml (pruebas en cada push) y
│                          refresh-catalog.yml (actualización semanal)
├── .env.example           Configuración de ejemplo (copiar como .env)
│
├── tomo/                  Backend
│   ├── devdb.py           Levanta la base local (Docker) al arrancar
│   ├── jobs.py            Tareas por línea de comandos (actualizar catálogo)
│   ├── scheduler.py       Actualización automática del catálogo
│   ├── server.py          Rutas de la API y arranque de Flask
│   ├── auth.py            Cuentas: registro, login, sesiones
│   ├── store.py           Todo el SQL (el resto de la app no escribe SQL)
│   ├── db.py              Pool de conexiones y migraciones
│   ├── library.py         Lógica de la biblioteca de tomos
│   ├── config.py          Rutas y configuración
│   ├── legacy_import.py   Importa los data/*.json de versiones anteriores
│   └── sources/           Fuentes externas
│       ├── argentina_catalog.py   Ivrea, calendario y emparejado de títulos
│       ├── jikan_client.py        API de Tenrai (compatible con Jikan)
│       └── manga_list.py          Lector de la exportación de MyAnimeList
│
├── tests/                 Pruebas automáticas (pytest)
├── migrations/            Esquema de la base, en archivos SQL numerados
├── templates/index.html   Página única
├── static/
│   ├── css/style.css      Estilos (modo oscuro)
│   └── js/                auth.js (acceso), script.js (Descubrir),
│                          argentina.js (En Argentina y Mi biblioteca)
└── data/                  Cachés y diagnósticos locales (no se sube a Git)
```

Para agregar algo nuevo: una tabla o columna va en un archivo nuevo de
`migrations/` (por ejemplo `0003_...sql`) y su SQL en `tomo/store.py`; una
fuente de datos nueva va en `tomo/sources/`; una ruta nueva, en
`tomo/server.py`.

## Cómo usarlo

Requisitos: Python 3.12 o superior (la versión con la que se prueba) y una
base PostgreSQL 14 o superior (el modo más simple es Docker Desktop).

**Desde cero en Windows:** instalá [Python](https://www.python.org/downloads/)
(marcando *Add python.exe to PATH*), [Docker Desktop](https://www.docker.com/products/docker-desktop/)
y [Git](https://git-scm.com/). Después:

```bash
git clone https://github.com/<tu-usuario>/Tomo.git
cd Tomo
INICIAR.bat
```

La primera vez tarda unos minutos (descarga la imagen de PostgreSQL e instala
las dependencias). Se abre en http://127.0.0.1:5000.

### 1. Base de datos

La forma más simple es Docker (con Docker Desktop instalado):

```bash
docker compose up -d
```

Eso levanta PostgreSQL con los datos y credenciales que la app espera por
defecto. No hace falta hacerlo a mano: al ejecutar `python app.py` (o
`INICIAR.bat`), si la base local no responde, Tomo abre Docker Desktop si está
apagado, levanta el contenedor y espera a que esté listo. Si no puede, explica
qué falta (`tomo/devdb.py`).

Si preferís un PostgreSQL instalado en tu computadora, creá el usuario y la
base y, si usás otros datos de conexión, indicale la dirección a Tomo:

```sql
CREATE USER tomo WITH PASSWORD 'tomo';
CREATE DATABASE tomo OWNER tomo;
```

La dirección se configura con la variable `DATABASE_URL`, o copiando
`.env.example` como `.env` y editándolo. Por defecto es
`postgresql://tomo:tomo@localhost:5432/tomo`.

### 2. Iniciar la app

En Windows alcanza con ejecutar `INICIAR.bat`: crea el entorno virtual, instala las dependencias y abre el servidor. Después
entrá a http://127.0.0.1:5000.

A mano, en cualquier sistema:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Al arrancar, la app crea las tablas que falten. Si venís de una versión
anterior (que guardaba todo en `data/*.json`), la primera vez importa
automáticamente tu biblioteca, tu lista de MyAnimeList y los catálogos. Los
archivos originales no se borran. Para repetir la importación a mano:
`python -m tomo.legacy_import --force`.

### 3. Primeros pasos

1. Creá tu cuenta en la pantalla de acceso. Si venís de la versión sin
   cuentas, la **primera cuenta que se crea conserva los datos de antes**
   (biblioteca, lista de MAL, correcciones). Las siguientes arrancan vacías.
2. Exportá tu lista desde MyAnimeList (`.xml` o `.xml.gz`) y subila en la
   pantalla de inicio.
3. Para la sección "En Argentina", el catálogo se actualiza solo mientras la
   app está abierta (la primera vez tarda unos 10 minutos y podés seguir
   usando la app). También podés apretar **Actualizar catálogo**.

### Si algo falla

| Síntoma | Qué hacer |
| --- | --- |
| `No pude conectarme a PostgreSQL` | Tomo intenta abrir Docker Desktop solo; esperá a que termine. Si no lo logra, abrilo a mano y esperá a *Engine running*. |
| El puerto 5432 está ocupado | Hay otro PostgreSQL instalado. Cerralo o cambiá `DATABASE_URL` en `.env`. |
| PowerShell dice que no puede ejecutar `Activate.ps1` | Usá `INICIAR.bat` (no necesita activar nada) o ejecutá una vez `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| La página se ve vieja o sin estilos | Recargá sin caché (Ctrl+F5). |
| "En Argentina" aparece vacío | El catálogo se actualiza solo; la primera vez tarda ~10 minutos. Mirá el avance en la barra de esa sección. |

### Actualización del catálogo

El catálogo de Ivrea, las listas de MAL de Argentina y el calendario son
compartidos: los actualiza una sola tarea para todos, no una por usuario.

- Con la app abierta, se actualiza solo cuando pasaron más de 24 horas
  (`TOMO_AUTO_REFRESH_HOURS`; con `0` se apaga).
- A mano o desde un programador (cron, Programador de tareas de Windows):
  `python -m tomo.jobs refresh` (con `--force` relee todo) y
  `python -m tomo.jobs status` para ver fechas y estado.
- Nunca corren dos a la vez. La app web muestra el progreso aunque la
  actualización la haya lanzado otro proceso, y recarga el catálogo apenas
  cambia en la base.
- Cualquier usuario puede pedir una actualización normal (como máximo una por
  hora). Forzarla y bajar el ZIP de diagnóstico es solo del administrador: la
  primera cuenta que se creó, o los emails de `TOMO_ADMIN_EMAILS`.

### Variables de entorno

| Variable | Para qué sirve |
| --- | --- |
| `DATABASE_URL` | Dirección de PostgreSQL (por defecto, la de `docker-compose.yml`). |
| `SECRET_KEY` | Clave que firma las sesiones. Si no se define, se genera una vez y se guarda en la base. |
| `TOMO_HTTPS=1` | Marca la cookie de sesión como `Secure`. Activala cuando la app se sirve por HTTPS. |
| `TOMO_LAN=1` | Deja abrir la app desde otros aparatos de la misma red WiFi. |
| `TOMO_PROXY_HOPS` | Cuántos proxies hay delante de la app (1 en Render), para ver la IP real de cada visita. En tu computadora, sin definir. |
| `TOMO_AUTO_REFRESH_HOURS` | Cada cuántas horas la app actualiza sola el catálogo (24 por defecto; `0` lo desactiva). |
| `TOMO_ADMIN_EMAILS` | Emails con permiso de administrador, separados por coma. Si no se define, lo es la primera cuenta. |

## Pruebas automáticas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Son unas 90 pruebas: cuentas y sesiones, aislamiento entre usuarios, biblioteca,
lectura de las páginas de Ivrea y del calendario, seguridad de los archivos
subidos, permisos y candado de la actualización del catálogo, migraciones e
importación de datos viejos.

Usan **su propia base de datos** (`tomo_test`, en el mismo PostgreSQL local), que
se borra y se vuelve a crear en cada corrida; tu base real no se toca. Por
seguridad, las pruebas se niegan a correr si el nombre de la base no termina en
`_test`. Para usar otra dirección, definí `TOMO_TEST_DATABASE_URL`. No necesitan
internet: las descargas se reemplazan por funciones de prueba.

GitHub las corre solas en cada push (`.github/workflows/tests.yml`).

## Publicarlo online (gratis)

Un esquema gratis que funciona: **Render** para el servidor y **Neon** para la
base de datos. Los planes gratis cambian seguido; antes de empezar, mirá los
límites actuales en sus páginas.

Antes de empezar:
- Subí el proyecto a GitHub (`data/` y `.env` no se suben).
- **No subas tu base local.** Empezá con una base nueva y volvé a subir tu
  lista de MAL desde la app ya publicada. Así nadie puede quedarse con datos
  que "esperan a su dueño".

Pasos:
1. **Neon:** creá un proyecto y copiá la dirección de conexión **directa** (el
   servidor no debe llevar `-pooler`: el candado de la actualización del
   catálogo necesita una conexión normal). Tiene que terminar en
   `sslmode=require`.
2. **Render:** *New → Blueprint* y elegí tu repositorio (usa `render.yaml`).
   Render te pide dos valores: `DATABASE_URL` (la de Neon) y
   `TOMO_ADMIN_EMAILS` (tu email).
3. Cuando termine el deploy, abrí la dirección que te da Render, creá tu
   cuenta **con ese email** (queda como administrador) y subí tu exportación
   de MyAnimeList.
4. El catálogo de Argentina se actualiza solo poco después de la primera
   visita (tarda unos 10 minutos). Para que se mantenga al día aunque la app
   esté dormida, creá en GitHub el secreto `DATABASE_URL`
   (*Settings → Secrets and variables → Actions*) con la misma dirección de
   Neon: el flujo *Actualizar catálogo* corre cada lunes y también se puede
   lanzar a mano desde la pestaña *Actions*.

Qué esperar del plan gratis:
- **Los flujos programados de GitHub se apagan solos** si el repositorio pasa 60
  días sin actividad (con un commit de vez en cuando no pasa). Si ya se apagó,
  se vuelve a prender a mano en la pestaña *Actions*. Si el catálogo deja de
  actualizarse, mirá ahí primero.
- El servidor de Render se duerme tras 15 minutos sin visitas y la primera
  visita después tarda cerca de un minuto en despertarlo.
- La base de Neon también se duerme sola a los pocos minutos y despierta en una
  fracción de segundo. No uses la base gratis de Render: vence a los 30 días.
- Corre un solo proceso con varios hilos (`gunicorn.conf.py`), a propósito: la
  app guarda en memoria las búsquedas en curso y los contadores de intentos.

Con otro hosting que acepte Docker, usá el `Dockerfile` y definí las variables
de la tabla de arriba (`DATABASE_URL`, `SECRET_KEY`, `TOMO_HTTPS=1` y, si hay
proxy, `TOMO_PROXY_HOPS`).

Todavía no tiene: recuperación de contraseña por email, política de seguridad
de contenido (CSP) ni copias de seguridad propias.

## Hoja de ruta

1. ✅ Datos en PostgreSQL.
2. ✅ Usuarios: registro, inicio de sesión y datos separados por persona.
3. ✅ Actualización del catálogo de Ivrea como tarea programada, compartida.
4. ✅ Despliegue en la nube con HTTPS (Render + Neon, gratis).
5. ✅ Tests automáticos (pytest + GitHub Actions) y guía de instalación.

Ideas para después: conectar la cuenta de MyAnimeList para traer la lista sola
(OAuth), recuperación de contraseña por email, política de seguridad de
contenido (CSP) y copias de seguridad propias.

## Fuentes de datos y aviso

- [MyAnimeList](https://myanimelist.net) (tu exportación) y la API compatible
  con Jikan de [Tenrai](https://api.tenrai.org).
- [Editorial Ivrea Argentina](https://www.ivrea.com.ar): catálogo y calendario
  de salidas, leídos con pausas de un segundo entre pedidos y guardados en caché.

Proyecto personal, sin fines de lucro y sin relación con MyAnimeList, Tenrai ni
Ivrea. Los nombres, portadas y datos pertenecen a sus dueños. El lector de
Ivrea depende del formato actual del sitio y puede dejar de funcionar si
cambia.