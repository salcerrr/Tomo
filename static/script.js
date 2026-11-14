
const magazineSort = document.getElementById("magazineSort");
const magazineList = document.getElementById("magazineList");

const searchError = document.getElementById("searchError");

const catalogStatus = document.getElementById("catalogStatus");
const catalogProgress = document.getElementById("catalogProgress");
const catalogCounter = document.getElementById("catalogCounter");

const libraryStatus = document.getElementById("libraryStatus");
const listUpload = document.getElementById("listUpload");

const startPanel = document.getElementById("startPanel");
const searchButton = document.getElementById("searchButton");
const searchHint = document.getElementById("searchHint");

const catalogStatusBox = document.getElementById("catalogStatusBox");
const filtersPanel = document.getElementById("filtersPanel");
const catalogSection = document.getElementById("catalogSection");

const sortControls = document.getElementById("sortControls");
const catalogDescription = document.getElementById("catalogDescription");

const filterSearch = document.getElementById("filterSearch");
const clearFilterButton = document.getElementById("clearFilterButton");
const filtersSummary = document.getElementById("filtersSummary");
const activeFilterBar = document.getElementById("activeFilterBar");
const activeFilterLabel = document.getElementById("activeFilterLabel");
const activeFilterClear = document.getElementById("activeFilterClear");

let magazineSections = [];
let discoveryRunning = false;


// Cambia el estado de la búsqueda y el aspecto del botón.
function setDiscoveryRunning(running) {
    discoveryRunning = running;

    searchButton.disabled = running;

    // El ícono de carga solo gira mientras se busca.
    catalogStatusBox.classList.toggle("is-idle", !running);

    searchButton.textContent = running
        ? "Buscando..."
        : "Buscar de nuevo";
}


// Muestra el estado, los filtros y el catálogo (después de buscar).
function revealResults() {
    catalogStatusBox.classList.remove("hidden");
    filtersPanel.classList.remove("hidden");
    catalogSection.classList.remove("hidden");

    startPanel.classList.add("is-compact");
}
let mangaSort = null;


// --------------------------------
// Estado de los filtros
// --------------------------------

// Única fuente de verdad del filtro activo.
// Solo puede haber UN filtro: un género O un theme, nunca ambos.
//   type:  null | "genre" | "theme"
//   value: nombre del género/theme, o null
const activeFilter = {
    type: null,
    value: null
};

// Conteos globales (mangas únicos por mal_id).
let filterCounts = {
    uniqueTotal: 0,
    genres: [],
    themes: []
};

const FILTER_GROUPS = {
    genre: {
        label: "Género",
        container: document.getElementById("genreFilters"),
        moreButton: document.getElementById("genreMore"),
        total: document.getElementById("genreTotal"),
        expanded: false
    },
    theme: {
        label: "Theme",
        container: document.getElementById("themeFilters"),
        moreButton: document.getElementById("themeMore"),
        total: document.getElementById("themeTotal"),
        expanded: false
    }
};

// Cantidad de opciones visibles antes de "Ver todos".
const FILTER_COLLAPSED_LIMIT = 14;


// --------------------------------
// Utilidades
// --------------------------------

function wait(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}


async function getJSON(url, options = {}) {
    const response = await fetch(url, options);

    let data;

    try {
        data = await response.json();
    } catch {
        throw new Error(
            `El servidor devolvió una respuesta inválida (HTTP ${response.status}).`
        );
    }

    if (!response.ok || data.ok === false) {
        throw new Error(
            data.error || `Error HTTP ${response.status}`
        );
    }

    return data;
}


function showError(message) {
    searchError.textContent = message;
    searchError.classList.remove("hidden");
}


function clearError() {
    searchError.textContent = "";
    searchError.classList.add("hidden");
}


// --------------------------------
// Fechas de discusiones
// --------------------------------

function getMangaDiscussionTimestamp(manga) {
    if (!manga.chapter_date) {
        return null;
    }

    const timestamp = Date.parse(manga.chapter_date);

    return Number.isFinite(timestamp) ? timestamp : null;
}


function getMagazineDiscussionTimestamp(section) {
    if (!Array.isArray(section.results)) {
        return null;
    }

    const dates = section.results
        .map(getMangaDiscussionTimestamp)
        .filter(date => date !== null);

    if (dates.length === 0) {
        return null;
    }

    // Fecha de la discusión más reciente de esa revista.
    return Math.max(...dates);
}


// --------------------------------
// Ordenamiento de mangas
// --------------------------------

function createMangaSortControl() {
    const wrapper = document.createElement("div");
    wrapper.className = "manga-sort-control";

    const label = document.createElement("label");
    label.textContent = "Ordenar mangas:";

    const select = document.createElement("select");
    select.id = "mangaSort";
    select.className = "field";

    const options = [
        {
            value: "default",
            text: "Mejor puntuación"
        },
        {
            value: "date_desc",
            text: "Discusión más reciente"
        },
        {
            value: "date_asc",
            text: "Discusión más antigua"
        }
    ];

    options.forEach(option => {
        const element = document.createElement("option");

        element.value = option.value;
        element.textContent = option.text;

        select.appendChild(element);
    });

    select.addEventListener("change", renderCatalog);

    label.htmlFor = "mangaSort";
    label.textContent = "Ordenar mangas por";

    wrapper.className = "catalog-sort";

    wrapper.appendChild(label);
    wrapper.appendChild(select);

    sortControls.appendChild(wrapper);

    mangaSort = select;
}


function sortManga(results) {
    const mode = mangaSort.value;
    const sorted = [...results];

    if (mode === "default") {
        return sorted;
    }

    sorted.sort((a, b) => {
        const dateA = getMangaDiscussionTimestamp(a);
        const dateB = getMangaDiscussionTimestamp(b);

        const hasA = dateA !== null;
        const hasB = dateB !== null;

        if (hasA && !hasB) return -1;
        if (!hasA && hasB) return 1;
        if (!hasA && !hasB) return 0;

        if (mode === "date_desc") {
            return dateB - dateA;
        }

        if (mode === "date_asc") {
            return dateA - dateB;
        }

        return 0;
    });

    return sorted;
}


// --------------------------------
// Lista MAL
// --------------------------------

async function loadLibraryInfo() {
    try {
        const data = await getJSON("/api/list-info");

        libraryStatus.textContent =
            `${data.username || "MyAnimeList"} · ` +
            `${data.total} mangas · ` +
            `${data.reading_count} en Reading.`;

    } catch (error) {
        libraryStatus.textContent =
            "No se pudo leer tu exportación: " + error.message;
    }
}


// --------------------------------
// Ordenar revistas
// --------------------------------

function addMagazineDateSortOptions() {
    const options = [
        {
            value: "date_desc",
            text: "Discusión más reciente"
        },
        {
            value: "date_asc",
            text: "Discusión más antigua"
        }
    ];

    options.forEach(option => {
        const exists = Array.from(magazineSort.options).some(
            item => item.value === option.value
        );

        if (exists) {
            return;
        }

        const element = document.createElement("option");

        element.value = option.value;
        element.textContent = option.text;

        magazineSort.appendChild(element);
    });
}


function sortSections(sections) {
    const mode = magazineSort.value;
    const sorted = [...sections];

    sorted.sort((a, b) => {
        if (mode === "count_desc") {
            return (b.count ?? -1) - (a.count ?? -1);
        }

        if (mode === "count_asc") {
            return (a.count ?? Infinity) - (b.count ?? Infinity);
        }

        if (mode === "name_asc") {
            return a.name.localeCompare(
                b.name,
                "es",
                { sensitivity: "base" }
            );
        }

        if (mode === "name_desc") {
            return b.name.localeCompare(
                a.name,
                "es",
                { sensitivity: "base" }
            );
        }

        if (mode === "date_desc" || mode === "date_asc") {
            const dateA = getMagazineDiscussionTimestamp(a);
            const dateB = getMagazineDiscussionTimestamp(b);

            const hasA = dateA !== null;
            const hasB = dateB !== null;

            // Las revistas sin fechas quedan al final.
            if (hasA && !hasB) return -1;
            if (!hasA && hasB) return 1;
            if (!hasA && !hasB) return 0;

            if (mode === "date_desc") {
                return dateB - dateA;
            }

            return dateA - dateB;
        }

        return 0;
    });

    return sorted;
}


// --------------------------------
// Elementos de las tarjetas
// --------------------------------

function createPill(text, neutral = false) {
    const pill = document.createElement("span");

    pill.className = "meta-pill";

    if (neutral) {
        pill.classList.add("neutral");
    }

    pill.textContent = text;

    return pill;
}


// --------------------------------
// Géneros y Themes de MAL
// --------------------------------

function createTagGroup(label, items, type) {
    if (!Array.isArray(items) || items.length === 0) {
        return null;
    }

    const group = document.createElement("div");
    group.className = `manga-tags manga-tags-${type}`;

    const heading = document.createElement("span");
    heading.className = "manga-tags-label";
    heading.textContent = label;

    group.appendChild(heading);

    const tags = document.createElement("div");
    tags.className = "manga-tags-list";

    items.forEach(item => {
        if (!item) return;

        const tag = document.createElement("span");

        tag.className = `manga-tag manga-tag-${type}`;
        tag.textContent = item;

        tags.appendChild(tag);
    });

    if (tags.childElementCount === 0) {
        return null;
    }

    group.appendChild(tags);

    return group;
}


// --------------------------------
// Información del foro
// --------------------------------

function createForumInfo(manga) {
    const forumInfo = document.createElement("div");
    forumInfo.className = "manga-forum-info";

    if (manga.forum_status === "found") {
        const chapter = document.createElement("span");

        chapter.className = "manga-forum-chapter";
        chapter.textContent =
            `Último capítulo detectado: ${manga.latest_chapter}`;

        forumInfo.appendChild(chapter);

        if (manga.chapter_date) {
            const date = document.createElement("span");

            date.className = "manga-forum-date";

            const datePart = manga.chapter_date.slice(0, 10);
            const parts = datePart.split("-");

            if (parts.length === 3) {
                date.textContent =
                    `Discusión creada: ${parts[2]}/${parts[1]}/${parts[0]}`;
            } else {
                date.textContent =
                    `Discusión creada: ${datePart}`;
            }

            forumInfo.appendChild(date);
        }

        if (manga.chapter_url) {
            const link = document.createElement("a");

            link.className = "manga-forum-link";
            link.href = manga.chapter_url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "Ver discusión ↗";

            forumInfo.appendChild(link);
        }

    } else if (manga.forum_status === "empty") {
        forumInfo.textContent =
            "No se encontraron discusiones de capítulos.";

    } else if (manga.forum_status === "error") {
        forumInfo.textContent =
            "No se pudo consultar el foro.";

    } else {
        forumInfo.textContent =
            "Buscando último capítulo...";
    }

    return forumInfo;
}


// --------------------------------
// Tarjeta de manga
// --------------------------------

function createMangaCard(manga) {
    const card = document.createElement("article");
    card.className = "manga-card";

    if (manga.image) {
        const image = document.createElement("img");

        image.className = "cover";
        image.src = manga.image;
        image.alt = manga.title || "Portada";
        image.loading = "lazy";

        image.onerror = () => {
            const placeholder = document.createElement("div");

            placeholder.className = "cover-placeholder";
            placeholder.textContent = "巻";

            image.replaceWith(placeholder);
        };

        card.appendChild(image);

    } else {
        const placeholder = document.createElement("div");

        placeholder.className = "cover-placeholder";
        placeholder.textContent = "巻";

        card.appendChild(placeholder);
    }

    const info = document.createElement("div");
    info.className = "manga-info";

    const title = document.createElement("h3");

    title.className = "manga-title";
    title.textContent = manga.title || "Sin título";

    info.appendChild(title);

    if (
        manga.title_english &&
        manga.title_english !== manga.title
    ) {
        const englishTitle = document.createElement("p");

        englishTitle.className = "english-title";
        englishTitle.textContent = manga.title_english;

        info.appendChild(englishTitle);
    }

    const pills = document.createElement("div");
    pills.className = "manga-meta";

    if (manga.score !== null && manga.score !== undefined) {
        pills.appendChild(
            createPill(`★ ${Number(manga.score).toFixed(2)}`)
        );
    }

    if (manga.chapters !== null && manga.chapters !== undefined) {
        pills.appendChild(
            createPill(`${manga.chapters} caps.`, true)
        );
    }

    if (manga.members !== null && manga.members !== undefined) {
        pills.appendChild(
            createPill(
                `${Number(manga.members).toLocaleString("es-AR")} miembros`,
                true
            )
        );
    }

    info.appendChild(pills);

    info.appendChild(createForumInfo(manga));

    const genres = createTagGroup(
        "Géneros",
        manga.genres,
        "genre"
    );

    if (genres) {
        info.appendChild(genres);
    }

    const themes = createTagGroup(
        "Themes",
        manga.themes,
        "theme"
    );

    if (themes) {
        info.appendChild(themes);
    }

    if (manga.synopsis) {
        const synopsis = document.createElement("p");

        synopsis.className = "synopsis";
        synopsis.textContent = manga.synopsis;

        info.appendChild(synopsis);
    }

    if (manga.url) {
        const link = document.createElement("a");

        link.className = "manga-link";
        link.href = manga.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Ver en MAL ↗";

        info.appendChild(link);
    }

    card.appendChild(info);

    return card;
}


// --------------------------------
// Filtros: conteos globales
// --------------------------------

function compareFilterOptions(a, b) {
    // Mayor cantidad primero; si empatan, orden alfabético.
    if (b.count !== a.count) {
        return b.count - a.count;
    }

    return a.name.localeCompare(
        b.name,
        "es",
        { sensitivity: "base" }
    );
}


function computeFilterCounts(sections) {
    // 1. Un único registro por mal_id, aunque el manga aparezca
    //    en varias revistas. Solo cuentan las revistas ya recuperadas
    //    con éxito; las pendientes o con error no aportan nada.
    const uniqueManga = new Map();

    for (const section of sections) {
        if (
            section.status !== "completed" ||
            !Array.isArray(section.results)
        ) {
            continue;
        }

        for (const manga of section.results) {
            if (manga.mal_id === null || manga.mal_id === undefined) {
                continue;
            }

            if (!uniqueManga.has(manga.mal_id)) {
                uniqueManga.set(manga.mal_id, manga);
            }
        }
    }

    // 2. Cada manga único suma una vez por cada género/theme que tiene.
    const genreCounts = new Map();
    const themeCounts = new Map();

    const addAll = (names, counts) => {
        if (!Array.isArray(names)) {
            return;
        }

        // Set: evita contar dos veces un nombre repetido en un mismo manga.
        for (const name of new Set(names)) {
            if (!name) continue;

            counts.set(name, (counts.get(name) || 0) + 1);
        }
    };

    for (const manga of uniqueManga.values()) {
        addAll(manga.genres, genreCounts);
        addAll(manga.themes, themeCounts);
    }

    const toSortedList = counts =>
        Array.from(counts, ([name, count]) => ({ name, count }))
            .sort(compareFilterOptions);

    return {
        uniqueTotal: uniqueManga.size,
        genres: toSortedList(genreCounts),
        themes: toSortedList(themeCounts)
    };
}


function refreshFilterCounts() {
    filterCounts = computeFilterCounts(magazineSections);

    renderFilterOptions();
}


// --------------------------------
// Filtros: selección única y excluyente
// --------------------------------

function isFilterActive(type, value) {
    return activeFilter.type === type && activeFilter.value === value;
}


function mangaMatchesFilter(manga) {
    if (activeFilter.type === null) {
        return true;
    }

    const names = activeFilter.type === "genre"
        ? manga.genres
        : manga.themes;

    return Array.isArray(names) && names.includes(activeFilter.value);
}


// Devuelve una copia filtrada. Nunca modifica el array original.
function applyFilter(results) {
    if (activeFilter.type === null) {
        return results;
    }

    return results.filter(mangaMatchesFilter);
}


let skipScrollRestore = false;


function onFilterChanged() {
    // Cambió el contenido de los carruseles: arrancan desde el principio.
    skipScrollRestore = true;

    renderFilterOptions();
    renderCatalog();
}


function toggleFilter(type, value) {
    if (isFilterActive(type, value)) {
        // Volver a pulsar la opción activa la quita.
        activeFilter.type = null;
        activeFilter.value = null;

    } else {
        // Reemplaza por completo el filtro anterior, sea del tipo que sea.
        activeFilter.type = type;
        activeFilter.value = value;
    }

    onFilterChanged();
}


function clearFilter() {
    if (activeFilter.type === null) {
        return;
    }

    activeFilter.type = null;
    activeFilter.value = null;

    onFilterChanged();
}


// --------------------------------
// Filtros: dibujar los controles
// --------------------------------

function createFilterChip(type, option) {
    const chip = document.createElement("button");

    chip.type = "button";
    chip.className = `filter-chip filter-chip-${type}`;
    chip.dataset.type = type;
    chip.dataset.value = option.name;

    const active = isFilterActive(type, option.name);

    if (active) {
        chip.classList.add("active");
    }

    chip.setAttribute("aria-pressed", active ? "true" : "false");

    const name = document.createElement("span");
    name.className = "filter-chip-name";
    name.textContent = option.name;

    const count = document.createElement("span");
    count.className = "filter-chip-count";
    count.textContent = option.count;

    chip.appendChild(name);
    chip.appendChild(count);

    return chip;
}


function renderFilterGroup(type, options, searchText) {
    const group = FILTER_GROUPS[type];

    group.total.textContent = options.length;

    let visible = options;

    if (searchText) {
        visible = options.filter(option =>
            option.name.toLowerCase().includes(searchText)
        );
    }

    let hiddenCount = 0;

    if (!searchText && !group.expanded &&
        visible.length > FILTER_COLLAPSED_LIMIT) {

        const shown = visible.slice(0, FILTER_COLLAPSED_LIMIT);

        // La opción activa siempre tiene que verse.
        const active = visible.find(option =>
            isFilterActive(type, option.name)
        );

        if (active && !shown.includes(active)) {
            shown.push(active);
        }

        hiddenCount = visible.length - shown.length;
        visible = shown;
    }

    const fragment = document.createDocumentFragment();

    visible.forEach(option => {
        fragment.appendChild(createFilterChip(type, option));
    });

    if (visible.length === 0) {
        const empty = document.createElement("span");

        empty.className = "filter-empty";
        empty.textContent = searchText
            ? "Sin coincidencias."
            : "Todavía no hay opciones.";

        fragment.appendChild(empty);
    }

    group.container.replaceChildren(fragment);

    // Botón "Ver todos / Ver menos".
    const canToggle =
        !searchText &&
        options.length > FILTER_COLLAPSED_LIMIT;

    group.moreButton.classList.toggle("hidden", !canToggle);

    if (canToggle) {
        group.moreButton.textContent = group.expanded
            ? "Ver menos"
            : `Ver todos (+${hiddenCount})`;
    }
}


function renderFilterSummary() {
    clearFilterButton.disabled = activeFilter.type === null;

    activeFilterBar.classList.toggle(
        "hidden",
        activeFilter.type === null
    );

    if (activeFilter.type !== null) {
        activeFilterLabel.textContent =
            `${FILTER_GROUPS[activeFilter.type].label}: ` +
            activeFilter.value;
    }

    const running = discoveryRunning ? " · búsqueda en curso" : "";

    if (activeFilter.type === null) {
        if (filterCounts.uniqueTotal === 0) {
            filtersSummary.textContent = discoveryRunning
                ? "Esperando los primeros resultados..."
                : "No hay mangas para filtrar.";

        } else {
            filtersSummary.textContent =
                `${filterCounts.uniqueTotal} mangas únicos · ` +
                `${filterCounts.genres.length} géneros · ` +
                `${filterCounts.themes.length} themes${running}`;
        }

        catalogDescription.textContent =
            "Cada revista muestra los mangas en publicación " +
            "que encontramos en ella.";

        return;
    }

    const list = activeFilter.type === "genre"
        ? filterCounts.genres
        : filterCounts.themes;

    const entry = list.find(item => item.name === activeFilter.value);
    const total = entry ? entry.count : 0;
    const label = FILTER_GROUPS[activeFilter.type].label;

    filtersSummary.textContent =
        `Filtro activo — ${label}: ${activeFilter.value} · ` +
        `${total} mangas únicos${running}`;

    catalogDescription.textContent =
        `Mostrando solo los mangas con ${label}: ` +
        `${activeFilter.value}. Las revistas sin coincidencias ` +
        `quedan atenuadas.`;
}


function renderFilterOptions() {
    // Conservamos el foco si el usuario navega con el teclado.
    const focused = document.activeElement;

    let refocus = null;

    if (focused && focused.classList &&
        focused.classList.contains("filter-chip")) {

        refocus = {
            type: focused.dataset.type,
            value: focused.dataset.value
        };
    }

    const searchText = filterSearch.value.trim().toLowerCase();

    renderFilterGroup("genre", filterCounts.genres, searchText);
    renderFilterGroup("theme", filterCounts.themes, searchText);

    renderFilterSummary();

    if (refocus) {
        const chip = Array.from(
            document.querySelectorAll(".filter-chip")
        ).find(item =>
            item.dataset.type === refocus.type &&
            item.dataset.value === refocus.value
        );

        if (chip) {
            chip.focus();
        }
    }
}


function initializeFilters() {
    Object.entries(FILTER_GROUPS).forEach(([type, group]) => {
        // Delegación de eventos: un solo listener por grupo.
        group.container.addEventListener("click", event => {
            const chip = event.target.closest(".filter-chip");

            if (!chip) return;

            toggleFilter(chip.dataset.type, chip.dataset.value);
        });

        group.moreButton.addEventListener("click", () => {
            group.expanded = !group.expanded;

            renderFilterOptions();
        });
    });

    clearFilterButton.addEventListener("click", clearFilter);
    activeFilterClear.addEventListener("click", clearFilter);

    filterSearch.addEventListener("input", renderFilterOptions);

    renderFilterOptions();
}


// --------------------------------
// Secciones de revistas
// --------------------------------

function createMagazineSection(section) {
    const container = document.createElement("section");

    container.className = "magazine-section";
    container.dataset.magazine = section.name;

    const heading = document.createElement("div");
    heading.className = "magazine-heading";

    const title = document.createElement("h3");

    title.className = "magazine-title";
    title.textContent = section.name;

    heading.appendChild(title);

    const count = document.createElement("span");
    count.className = "magazine-count";

    if (section.status === "pending") {
        count.textContent = "Pendiente";

    } else if (
        section.status === "error" ||
        section.status === "unavailable"
    ) {
        count.textContent = "Error";

    } else if (activeFilter.type !== null) {
        const matching = applyFilter(section.results || []).length;

        count.textContent =
            `${matching} de ${section.count ?? 0} mangas`;

        if (matching > 0) {
            count.classList.add("magazine-count-match");
        }

    } else {
        count.textContent = `${section.count ?? 0} mangas`;
    }

    heading.appendChild(count);
    container.appendChild(heading);

    if (section.status === "pending") {
        const pending = document.createElement("div");

        pending.className = "magazine-pending";
        pending.textContent = "Esperando consulta...";

        container.appendChild(pending);

        return container;
    }

    if (
        section.status === "error" ||
        section.status === "unavailable"
    ) {
        const error = document.createElement("div");

        error.className = "magazine-error";

        error.textContent =
            section.error || "No se pudo consultar esta revista.";

        container.appendChild(error);

        return container;
    }

    if (!section.results || section.results.length === 0) {
        const empty = document.createElement("div");

        empty.className = "magazine-empty";

        const icon = document.createElement("span");

        icon.className = "magazine-empty-icon";
        icon.textContent = "∅";

        empty.appendChild(icon);

        const content = document.createElement("div");

        const strong = document.createElement("strong");
        strong.textContent = "No se encontraron mangas";

        const description = document.createElement("p");

        description.textContent =
            "No hay mangas en publicación en esta revista.";

        content.appendChild(strong);
        content.appendChild(description);

        empty.appendChild(content);
        container.appendChild(empty);

        return container;
    }

    // El filtro es solo de visualización: section.results no se toca.
    const visibleResults = applyFilter(section.results);

    if (visibleResults.length === 0) {
        container.classList.add("magazine-section-dim");

        const noMatch = document.createElement("div");

        noMatch.className = "magazine-nomatch";

        const label = FILTER_GROUPS[activeFilter.type].label;

        noMatch.textContent =
            `Ningún manga de esta revista tiene ` +
            `${label}: ${activeFilter.value}.`;

        container.appendChild(noMatch);

        return container;
    }

    const rail = document.createElement("div");
    rail.className = "manga-rail";

    sortManga(visibleResults).forEach(manga => {
        rail.appendChild(createMangaCard(manga));
    });

    container.appendChild(rail);

    return container;
}


// --------------------------------
// Renderizar catálogo
// --------------------------------

function renderCatalog() {
    const scrollPositions = new Map();

    const restoreScroll = !skipScrollRestore;

    skipScrollRestore = false;

    magazineList.querySelectorAll(".magazine-section").forEach(section => {
        const rail = section.querySelector(".manga-rail");

        if (rail && restoreScroll) {
            scrollPositions.set(
                section.dataset.magazine,
                rail.scrollLeft
            );
        }
    });

    const sorted = sortSections(magazineSections);

    const fragment = document.createDocumentFragment();

    sorted.forEach(section => {
        fragment.appendChild(createMagazineSection(section));
    });

    magazineList.replaceChildren(fragment);

    magazineList.querySelectorAll(".magazine-section").forEach(section => {
        const rail = section.querySelector(".manga-rail");

        if (rail && scrollPositions.has(section.dataset.magazine)) {
            rail.scrollLeft = scrollPositions.get(
                section.dataset.magazine
            );
        }
    });
}


function updateSection(section) {
    const index = magazineSections.findIndex(
        item => item.name === section.name
    );

    if (index !== -1) {
        magazineSections[index] = section;
    } else {
        magazineSections.push(section);
    }

    // Llegó una sección nueva: se reconstruyen los conteos globales.
    refreshFilterCounts();

    renderCatalog();
}


// --------------------------------
// Inicializar las revistas
// --------------------------------

function initializeMagazines(magazines) {
    magazineSections = magazines.map(magazine => ({
        name: magazine.name,
        status: "pending",
        count: null,
        results: []
    }));

    // Nueva búsqueda: los conteos se reinician desde cero.
    refreshFilterCounts();

    renderCatalog();

    catalogCounter.textContent =
        `0 / ${magazines.length}`;
}


// --------------------------------
// Actualizar información de un manga
// --------------------------------

function updateMangaForum(update) {
    for (const section of magazineSections) {
        if (!section.results) {
            continue;
        }

        const manga = section.results.find(
            item => item.mal_id === update.mal_id
        );

        if (manga) {
            Object.assign(manga, update);
            return true;
        }
    }

    return false;
}


// --------------------------------
// Consultar el progreso
// --------------------------------

async function pollDiscovery(jobId) {
    let offset = 0;
    let forumOffset = 0;
    let magazinesInitialized = false;

    while (true) {
        const data = await getJSON(
            `/api/discover-status/${jobId}?after=${offset}&forum_after=${forumOffset}`
        );

        // Las revistas se detectan en el servidor a partir de los
        // mangas en Reading; recién se conocen cuando termina esa etapa.
        if (!magazinesInitialized && data.magazines.length > 0) {
            initializeMagazines(
                data.magazines.map(name => ({ name }))
            );

            magazinesInitialized = true;
        }

        for (const section of data.sections) {
            updateSection(section);
            offset++;
        }

        let forumChanged = false;

        for (const update of data.forum_updates) {
            if (updateMangaForum(update)) {
                forumChanged = true;
            }

            forumOffset++;
        }

        if (forumChanged) {
            renderCatalog();
        }

        catalogCounter.textContent =
            `${data.completed_count} / ${data.total_magazines}`;

        if (data.phase === "magazines") {
            catalogStatus.textContent =
                "Detectando las revistas de tu lista...";

            catalogProgress.textContent =
                data.current_page > 0
                    ? `Manga ${data.current_page} de ${data.total_pages}`
                    : data.message || "Consultando tus mangas en Reading.";

        } else if (data.phase === "catalogue") {
            catalogStatus.textContent =
                "Descargando catálogo de revistas...";

            catalogProgress.textContent =
                data.current_page > 0
                    ? `Página ${data.current_page} de ${data.total_pages}`
                    : data.message || "Preparando catálogo.";

        } else if (data.phase === "search") {
            let message =
                `Revista ${data.current_index} de ${data.total_magazines}: ` +
                data.current_magazine;

            if (data.current_page > 0) {
                message +=
                    ` · Página ${data.current_page} de ${data.total_pages}`;
            }

            catalogStatus.textContent =
                "Buscando mangas...";

            catalogProgress.textContent = message;

        } else if (data.phase === "forum") {
            catalogStatus.textContent =
                "Buscando últimos capítulos...";

            catalogProgress.textContent =
                `${data.forum_completed} de ${data.forum_total} mangas consultados` +
                ` · ${data.forum_errors} errores`;

        } else {
            catalogStatus.textContent =
                "Preparando búsquedas...";

            catalogProgress.textContent =
                data.message || "Iniciando consultas.";
        }

        if (data.status === "completed" && data.fatal_error) {
            catalogStatus.textContent =
                "No se pudo completar el catálogo.";

            catalogProgress.textContent = data.fatal_error;

            showError(data.fatal_error);

            setDiscoveryRunning(false);

            renderFilterSummary();

            return;
        }

        if (data.status === "completed") {
            const successful = magazineSections.filter(
                section => section.status === "completed"
            );

            const totalManga = successful.reduce(
                (total, section) => total + section.count,
                0
            );

            const emptyMagazines = successful.filter(
                section => section.count === 0
            ).length;

            const cacheHits = successful.filter(
                section => section.cache_hit
            ).length;

            catalogStatus.textContent =
                "Catálogo completado";

            if (data.total_magazines === 0) {
                catalogProgress.textContent = data.message;

                setDiscoveryRunning(false);

                renderFilterSummary();

                return;
            }

            catalogProgress.textContent =
                `${successful.length} revistas consultadas · ` +
                `${totalManga} mangas encontrados ` +
                `(${filterCounts.uniqueTotal} únicos) · ` +
                `${emptyMagazines} sin resultados · ` +
                `${data.errors} revistas con problemas · ` +
                `${data.forum_completed} foros consultados · ` +
                `${data.forum_errors} errores de foro · ` +
                `${cacheHits} revistas desde caché` +
                (data.lookup_failed > 0
                    ? ` · ${data.lookup_failed} mangas de tu lista ` +
                      `sin revista detectada`
                    : "") +
                ".";

            setDiscoveryRunning(false);

            renderFilterSummary();

            return;
        }

        await wait(600);
    }
}


// --------------------------------
// Iniciar búsqueda de todas
// --------------------------------

async function startDiscovery() {
    if (discoveryRunning) return;

    setDiscoveryRunning(true);

    revealResults();

    clearError();

    magazineList.innerHTML = "";

    // No se mezclan resultados de una búsqueda anterior.
    magazineSections = [];
    refreshFilterCounts();

    catalogStatus.textContent =
        "Preparando catálogo...";

    catalogProgress.textContent =
        "Iniciando consultas a Tenrai.";

    catalogCounter.textContent = "0 / –";

    try {
        const started = await getJSON("/api/discover");

        initializeMagazines(started.magazines);

        catalogCounter.textContent = "0 / –";

        catalogStatus.textContent =
            "Detectando las revistas de tu lista...";

        catalogProgress.textContent =
            "Consultando las revistas de tus mangas en Reading.";

        await pollDiscovery(started.job_id);

    } catch (error) {
        catalogStatus.textContent =
            "No se pudo completar el catálogo.";

        catalogProgress.textContent =
            error.message;

        showError(error.message);

        setDiscoveryRunning(false);

        renderFilterSummary();
    }
}


// --------------------------------
// Actualizar exportación MAL
// --------------------------------

async function uploadList() {
    const file = listUpload.files[0];

    if (!file) return;

    clearError();

    if (discoveryRunning) {
        showError(
            "Esperá a que termine la búsqueda antes de actualizar tu lista."
        );

        listUpload.value = "";

        return;
    }

    catalogStatus.textContent =
        "Actualizando colección...";

    catalogProgress.textContent =
        "Leyendo tu nueva exportación de MyAnimeList.";

    const formData = new FormData();

    formData.append("file", file);

    try {
        const data = await getJSON(
            "/api/upload-list",
            {
                method: "POST",
                body: formData
            }
        );

        libraryStatus.textContent =
            `${data.username || "MyAnimeList"} · ` +
            `${data.total} mangas · ` +
            `${data.reading_count} en Reading.`;

        catalogStatus.textContent =
            "Lista actualizada";

        catalogProgress.textContent =
            "Apretá Buscar de nuevo para actualizar el catálogo.";

        searchHint.textContent =
            "Cargaste una lista nueva. Buscá para ver los cambios.";

    } catch (error) {
        showError(error.message);

        catalogStatus.textContent =
            "No se pudo actualizar la colección.";

        catalogProgress.textContent =
            error.message;

    } finally {
        listUpload.value = "";
    }
}


// --------------------------------
// Eventos
// --------------------------------

magazineSort.addEventListener(
    "change",
    renderCatalog
);

listUpload.addEventListener(
    "change",
    uploadList
);


// --------------------------------
// Inicio
// --------------------------------

async function initialize() {
    addMagazineDateSortOptions();

    createMangaSortControl();

    initializeFilters();

    searchButton.addEventListener("click", startDiscovery);

    // La búsqueda ya no empieza sola: la inicia el botón.
    await loadLibraryInfo();
}


initialize();
