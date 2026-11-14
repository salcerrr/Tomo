# Tomo

Aplicación web local para lectores de manga. A partir de tu exportación de
MyAnimeList, te ayuda a descubrir qué leer, saber cuáles de tus mangas salen en
papel en Argentina, y llevar el control de los tomos que tenés en tu estantería.

Todo corre en tu computadora: no hay cuentas ni servidores, y tus datos quedan
en la carpeta `data/`.

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
- **Datos:** archivos JSON locales con escritura atómica. Respeto del límite de
  pedidos de la API y pausas entre pedidos al scrapear.
- **Scraping:** lector del sitio de Ivrea con normalización de títulos para
  emparejar series entre fuentes distintas y resolver ambigüedades.

## Cómo usarlo

Requisitos: Python 3.10 o superior.

En Windows alcanza con ejecutar `INICIAR.bat`: crea el entorno virtual, instala
las dependencias y abre el servidor. Después entrá a http://127.0.0.1:5000.

A mano, en cualquier sistema:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

1. Exportá tu lista desde MyAnimeList (`.xml` o `.xml.gz`) y subila en la
   pantalla de inicio.
2. Para la sección "En Argentina", apretá **Actualizar catálogo** la primera
   vez (tarda unos 10 minutos y podés seguir usando la app).

## Fuentes de datos y aviso

- [MyAnimeList](https://myanimelist.net) (tu exportación) y la API compatible
  con Jikan de [Tenrai](https://api.tenrai.org).
- [Editorial Ivrea Argentina](https://www.ivrea.com.ar): catálogo y calendario
  de salidas, leídos con pausas de un segundo entre pedidos y guardados en caché.

Proyecto personal, sin fines de lucro y sin relación con MyAnimeList, Tenrai ni
Ivrea. Los nombres, portadas y datos pertenecen a sus dueños. El lector de
Ivrea depende del formato actual del sitio y puede dejar de funcionar si
cambia.