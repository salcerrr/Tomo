/* ============================================================
   Tomo: navegación, "En Argentina" y "Mi biblioteca"
   (la vista "Descubrir" está en script.js)
============================================================ */

(() => {
    "use strict";

    const $ = id => document.getElementById(id);

    const VIEWS = ["descubrir", "argentina", "biblioteca"];

    const STATUS_LABELS = {
        "Reading": "Leyendo",
        "Plan to Read": "Por leer",
        "Completed": "Completados",
        "On-Hold": "En pausa",
        "Dropped": "Abandonados",
        "Unknown": "Sin estado"
    };

    const KNOWN_PUBLISHERS = [
        "Ivrea", "Panini", "Ovni Press", "Distrito Manga", "Kemuri",
        "Planeta Cómic", "Utopía", "Pop Fiction", "Moztros", "Deuxstudio"
    ];


    // --------------------------------
    // Utilidades
    // --------------------------------

    async function api(url, options = {}) {
        const init = { ...options };

        if (init.body !== undefined && typeof init.body !== "string") {
            init.body = JSON.stringify(init.body);

            init.headers = {
                "Content-Type": "application/json",
                ...(init.headers || {})
            };
        }

        const response = await fetch(url, init);

        let data;

        try {
            data = await response.json();
        } catch {
            throw new Error(
                `El servidor devolvió una respuesta inválida (HTTP ${response.status}).`
            );
        }

        if (!response.ok || data.ok === false) {
            throw new Error(data.error || `Error HTTP ${response.status}`);
        }

        return data;
    }


    function el(tag, className, text) {
        const node = document.createElement(tag);

        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;

        return node;
    }


    function showBox(box, message) {
        box.textContent = message;
        box.classList.remove("hidden");
    }


    function hideBox(box) {
        box.textContent = "";
        box.classList.add("hidden");
    }


    function ago(timestamp) {
        if (!timestamp) return "nunca";

        const seconds = Math.max(0, Date.now() / 1000 - timestamp);

        if (seconds < 90) return "hace un momento";

        const minutes = Math.round(seconds / 60);

        if (minutes < 60) return `hace ${minutes} min`;

        const hours = Math.round(minutes / 60);

        if (hours < 36) return `hace ${hours} h`;

        const days = Math.round(hours / 24);

        return `hace ${days} días`;
    }


    function plural(count, one, many) {
        return `${count} ${count === 1 ? one : many}`;
    }


    function matchesText(text, query) {
        return (text || "").toLowerCase().includes(query);
    }


    // Aviso breve, con acción opcional (por ejemplo, "Deshacer").
    let toastTimer = null;

    function toast(message, options = {}) {
        const box = $("toast");

        box.replaceChildren();
        box.classList.toggle("toast-error", Boolean(options.error));

        box.appendChild(el("span", "toast-text", message));

        if (options.action) {
            const button = el("button", "toast-action", options.action.label);

            button.type = "button";

            button.addEventListener("click", () => {
                box.classList.add("hidden");
                options.action.handler();
            });

            box.appendChild(button);
        }

        box.classList.remove("hidden");

        clearTimeout(toastTimer);

        toastTimer = setTimeout(
            () => box.classList.add("hidden"),
            options.duration || 4500
        );
    }


    function coverNode(url, className) {
        if (url) {
            const image = el("img", className);

            image.src = url;
            image.alt = "";
            image.loading = "lazy";

            image.addEventListener("error", () => {
                image.replaceWith(el("div", `${className} cover-empty`, "巻"));
            });

            return image;
        }

        return el("div", `${className} cover-empty`, "巻");
    }


    function chipRow(container, options, current, onPick) {
        container.replaceChildren();

        options.forEach(option => {
            const chip = el("button", "chip");

            chip.type = "button";
            chip.setAttribute("aria-pressed", option.value === current);

            if (option.value === current) chip.classList.add("active");

            chip.appendChild(el("span", "chip-label", option.label));

            if (option.count !== undefined) {
                chip.appendChild(el("span", "chip-count", String(option.count)));
            }

            chip.addEventListener("click", () => onPick(option.value));

            container.appendChild(chip);
        });
    }


    // --------------------------------
    // Navegación
    // --------------------------------

    function currentView() {
        const name = location.hash.replace(/^#\/?/, "");

        return VIEWS.includes(name) ? name : "descubrir";
    }


    function showView() {
        const view = currentView();

        VIEWS.forEach(name => {
            $(`view-${name}`).classList.toggle("hidden", name !== view);
        });

        document.querySelectorAll(".nav-tab").forEach(tab => {
            const active = tab.dataset.view === view;

            tab.classList.toggle("active", active);

            if (active) {
                tab.setAttribute("aria-current", "page");
            } else {
                tab.removeAttribute("aria-current");
            }
        });

        if (view === "argentina") Argentina.enter();
        if (view === "biblioteca") Library.enter();
    }


    // Portadas que faltan: se piden a MyAnimeList de a una, en segundo plano.
    const coverTried = new Set();
    const coverQueue = [];
    let coverWorking = false;

    function paintCover(item) {
        document
            .querySelectorAll(
                `[data-id="${item.mal_id}"] .arg-cover, ` +
                `[data-id="${item.mal_id}"] .shelf-cover`
            )
            .forEach(node => {
                const fresh = coverNode(
                    item.image,
                    node.classList.contains("arg-cover")
                        ? "arg-cover"
                        : "shelf-cover"
                );

                node.replaceWith(fresh);
            });
    }

    async function coverWorker() {
        if (coverWorking) return;

        coverWorking = true;

        while (coverQueue.length) {
            const item = coverQueue.shift();

            try {
                const data = await api(`/api/argentina/cover/${item.mal_id}`);

                if (data.image) {
                    item.image = data.image;
                    paintCover(item);
                }

            } catch {
                // Sin conexión con MyAnimeList: se reintenta la próxima vez.
                coverTried.delete(item.mal_id);
                coverQueue.length = 0;
            }
        }

        coverWorking = false;
    }

    function fillCovers(items) {
        items.forEach(item => {
            if (item.image || coverTried.has(item.mal_id)) return;

            coverTried.add(item.mal_id);
            coverQueue.push(item);
        });

        coverWorker();
    }


    const UPCOMING_LABELS = {
        tomo: "Próximo tomo",
        reedicion: "Reedición",
        especial: "Edición especial"
    };

    function whenText(entry) {
        if (entry.days_left === null || entry.days_left === undefined) {
            return entry.date_label;
        }

        if (entry.days_left === 0) return `${entry.date_label} · hoy`;
        if (entry.days_left === 1) return `${entry.date_label} · mañana`;

        return `${entry.date_label} · en ${entry.days_left} días`;
    }

    // Estilos de las salidas: van acá para que se vean bien aunque el
    // navegador tenga guardado un style.css viejo.
    (() => {
        const style = document.createElement("style");

        style.textContent = `
            .arg-upcoming { list-style: none; margin: 6px 0 4px; padding: 0;
                display: grid; gap: 6px; }
            .arg-upcoming .up { display: flex; flex-wrap: wrap;
                align-items: baseline; gap: 2px 12px; padding: 7px 12px;
                border-left: 3px solid #8ea6ff;
                background: rgba(142, 166, 255, 0.12);
                border-radius: 0 6px 6px 0; font-size: 0.88rem; }
            .arg-upcoming .up-reedicion { border-left-color: #6cc4a1;
                background: rgba(108, 196, 161, 0.1); }
            .arg-upcoming .up-especial { border-left-color: #e3b552;
                background: rgba(227, 181, 82, 0.1); }
            .arg-upcoming .up-kind { color: #9aa2b0; min-width: 8.5em; }
            .arg-upcoming .up-volume { color: #e7e4dd; }
            .arg-upcoming .up-when { color: #e7e4dd; }
            .arg-upcoming .up-tag { padding: 0 8px; border-radius: 999px;
                border: 1px solid #2a313e; color: #9aa2b0;
                font-size: 0.74rem; }
        `;

        document.head.appendChild(style);
    })();

    function volumesText(volumes) {
        const sorted = [...new Set(volumes)].sort((a, b) => a - b);

        if (sorted.length === 1) return `Tomo ${sorted[0]}`;

        return `Tomos ${sorted.slice(0, -1).join(", ")} y ${sorted[sorted.length - 1]}`;
    }

    // Una línea por tipo de salida (la fecha más cercana de cada una).
    // Si ese día salen varios tomos, van juntos en la misma línea.
    function upcomingNode(item) {
        const entries = item.upcoming || [];

        if (entries.length === 0) return null;

        const list = el("ul", "arg-upcoming");

        const order = ["tomo", "especial", "reedicion"];

        order.forEach(kind => {
            const ofKind = entries.filter(entry => entry.kind === kind);

            if (ofKind.length === 0) return;

            const first = ofKind[0];

            const sameDay = ofKind.filter(entry => entry.date === first.date);

            const row = el("li", `up up-${kind}`);

            row.appendChild(el("span", "up-kind", UPCOMING_LABELS[kind]));

            row.appendChild(el(
                "strong", "up-volume",
                volumesText(sameDay.map(entry => entry.volume))
            ));

            row.appendChild(el("span", "up-when", whenText(first)));

            if (sameDay.some(entry => entry.last)) {
                row.appendChild(el("span", "up-tag", "Último tomo"));
            }

            if (sameDay.some(entry => entry.new)) {
                row.appendChild(el("span", "up-tag", "Serie nueva"));
            }

            list.appendChild(row);
        });

        return list;
    }


    // ============================================================
    // EN ARGENTINA
    // ============================================================

    const Argentina = {
        items: [],
        notFound: [],
        catalog: null,
        statusFilter: "all",
        publisher: "",
        query: "",
        notFoundQuery: "",
        pollTimer: null,
        wasRunning: false,

        init() {
            $("argSearch").addEventListener("input", event => {
                this.query = event.target.value.trim().toLowerCase();
                this.renderList();
            });

            $("argPublisher").addEventListener("change", event => {
                this.publisher = event.target.value;
                this.renderList();
            });

            $("notFoundSearch").addEventListener("input", event => {
                this.notFoundQuery = event.target.value.trim().toLowerCase();
                this.renderNotFound();
            });

            $("notFoundToggle").addEventListener("click", () => {
                const body = $("notFoundBody");
                const open = body.classList.toggle("hidden") === false;

                $("notFoundToggle").setAttribute("aria-expanded", open);
                $("notFoundSection").classList.toggle("open", open);
            });

            $("refreshCatalogButton").addEventListener("click", () => {
                this.refresh();
            });

            $("addManualButton").addEventListener("click", () => {
                ManualDialog.open({});
            });
        },

        async enter() {
            hideBox($("argError"));

            try {
                const data = await api("/api/argentina/list");

                this.items = data.items;
                this.notFound = data.not_found;
                this.catalog = data.catalog;

                this.renderAll();

                if (data.catalog.job_running) {
                    this.startPolling();

                } else {
                    this.refreshCalendar();
                }

            } catch (error) {
                showBox($("argError"), error.message);
            }
        },

        // El calendario es un solo pedido: se renueva solo si está viejo.
        async refreshCalendar() {
            if (this.calendarChecked) return;

            this.calendarChecked = true;

            try {
                const data = await api("/api/argentina/calendar", {
                    method: "POST"
                });

                if (data.updated && currentView() === "argentina") {
                    this.calendarChecked = true;
                    await this.enter();
                }

            } catch {
                /* sin conexión: se muestra lo guardado */
            }
        },

        async refresh() {
            hideBox($("argError"));

            try {
                await api("/api/argentina/refresh", { method: "POST" });

                this.wasRunning = true;

                this.startPolling();

            } catch (error) {
                showBox($("argError"), error.message);
            }
        },

        startPolling() {
            if (this.pollTimer) return;

            this.wasRunning = true;

            const tick = async () => {
                try {
                    const status = await api("/api/argentina/status");

                    this.catalog = status;

                    this.renderSource();

                    if (status.job_running) {
                        this.pollTimer = setTimeout(tick, 1500);
                        return;
                    }

                } catch (error) {
                    showBox($("argError"), error.message);
                }

                this.pollTimer = null;

                if (this.wasRunning) {
                    this.wasRunning = false;

                    if (this.catalog && this.catalog.job_last_error) {
                        toast(
                            "Terminó con avisos: " + this.catalog.job_last_error,
                            { error: true, duration: 9000 }
                        );
                    } else {
                        toast("Catálogo actualizado.");
                    }

                    if (currentView() === "argentina") this.enter();
                }
            };

            tick();
        },

        // ---- Fuentes y progreso

        renderSource() {
            const c = this.catalog;

            if (!c) return;

            const text = $("sourceText");

            $("debugLink").classList.toggle(
                "hidden",
                !(c.ivrea_unread > 0 || (!c.empty && c.calendar_count === 0))
            );

            text.classList.remove("warn");

            if (c.empty) {
                text.textContent =
                    "Todavía no hay catálogo. Apretá Actualizar catálogo " +
                    "para armarlo; la primera vez tarda unos 10 minutos y " +
                    "podés seguir usando Tomo mientras tanto.";

            } else {
                const parts = [];

                if (c.ivrea_count > 0) {
                    parts.push(
                        `Ivrea Argentina: ${plural(c.ivrea_count, "serie", "series")}`
                        + ` (${ago(c.ivrea_updated_at)})`
                    );
                } else {
                    parts.push("Ivrea Argentina: todavía sin leer");
                }

                parts.push(
                    `listas de MyAnimeList: ${c.stacks_count}`
                );

                if (c.calendar_count > 0) {
                    parts.push(
                        `calendario de salidas: ${c.calendar_count}`
                        + ` (${ago(c.calendar_updated_at)})`
                    );
                }

                if (c.manual_count > 0) {
                    parts.push(`cargados a mano: ${c.manual_count}`);
                }

                text.textContent = "Fuentes · " + parts.join(" · ");

                if (c.ivrea_unread > 0 && c.ivrea_unread >= c.ivrea_count * 0.2) {
                    text.textContent +=
                        `. No pude leer ${plural(c.ivrea_unread, "ficha", "fichas")} de Ivrea` +
                        ` (el sitio puede haber cambiado).`;

                    text.classList.add("warn");
                }

                if (c.calendar_error) {
                    text.textContent += `. ${c.calendar_error}`;

                    text.classList.add("warn");
                }
            }

            const running = Boolean(c.job_running);

            $("refreshCatalogButton").disabled = running;

            $("refreshCatalogButton").textContent = running
                ? "Actualizando..."
                : "Actualizar catálogo";

            const bar = $("catalogBar");

            bar.classList.toggle("hidden", !running);

            if (running) {
                const total = c.job_total || 0;
                const done = c.job_done || 0;

                $("catalogBarFill").style.width =
                    total > 0 ? `${Math.min(100, (done / total) * 100)}%` : "8%";

                $("catalogBarFill").classList.toggle("indeterminate", total === 0);

                $("catalogBarText").textContent =
                    (c.job_message || "Actualizando...") +
                    (c.job_errors ? ` · ${plural(c.job_errors, "error", "errores")}` : "");
            }
        },

        // ---- Lista

        renderAll() {
            this.renderSource();
            this.renderFilters();
            this.renderList();
            this.renderNotFound();
        },

        renderFilters() {
            const counts = {};

            this.items.forEach(item => {
                const key = item.user_status || "manual";

                counts[key] = (counts[key] || 0) + 1;
            });

            const order = [
                "Reading", "Plan to Read", "Completed",
                "On-Hold", "Dropped", "Unknown", "manual"
            ];

            const options = [{
                value: "all",
                label: "Todos",
                count: this.items.length
            }];

            order.forEach(key => {
                if (counts[key]) {
                    options.push({
                        value: key,
                        label: key === "manual"
                            ? "Fuera de mi lista"
                            : STATUS_LABELS[key],
                        count: counts[key]
                    });
                }
            });

            const withUpcoming = this.items.filter(
                item => (item.upcoming || []).length > 0
            ).length;

            if (withUpcoming > 0) {
                options.splice(1, 0, {
                    value: "upcoming",
                    label: "Con salidas próximas",
                    count: withUpcoming
                });
            }

            if (!options.some(o => o.value === this.statusFilter)) {
                this.statusFilter = "all";
            }

            chipRow(
                $("argStatusFilter"),
                options,
                this.statusFilter,
                value => {
                    this.statusFilter = value;
                    this.renderFilters();
                    this.renderList();
                }
            );

            const publishers = {};

            this.items.forEach(item => {
                const name = item.publisher || "Sin editorial";

                publishers[name] = (publishers[name] || 0) + 1;
            });

            const select = $("argPublisher");

            select.replaceChildren();

            const all = el("option", "", "Todas las editoriales");

            all.value = "";

            select.appendChild(all);

            Object.entries(publishers)
                .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "es"))
                .forEach(([name, count]) => {
                    const option = el("option", "", `${name} (${count})`);

                    option.value = name;

                    select.appendChild(option);
                });

            select.value = publishers[this.publisher] ? this.publisher : "";
            this.publisher = select.value;
        },

        visibleItems() {
            return this.items.filter(item => {
                if (this.statusFilter === "upcoming") {
                    if ((item.upcoming || []).length === 0) return false;

                } else if (this.statusFilter !== "all") {
                    const key = item.user_status || "manual";

                    if (key !== this.statusFilter) return false;
                }

                if (
                    this.publisher &&
                    (item.publisher || "Sin editorial") !== this.publisher
                ) {
                    return false;
                }

                if (this.query && !matchesText(item.title, this.query)) {
                    return false;
                }

                return true;
            });
        },

        renderList() {
            const list = $("argList");

            const visible = this.visibleItems();

            if (this.statusFilter === "upcoming") {
                visible.sort((a, b) =>
                    a.upcoming[0].date.localeCompare(b.upcoming[0].date) ||
                    a.title.localeCompare(b.title, "es")
                );
            }

            list.replaceChildren();

            $("argResultLine").textContent = this.items.length === 0
                ? ""
                : `${plural(visible.length, "manga", "mangas")} de ${this.items.length}`;

            if (this.items.length === 0) {
                const empty = el("div", "empty");

                empty.appendChild(el("strong", "", "Todavía no hay mangas acá"));

                empty.appendChild(el(
                    "p", "",
                    "Actualizá el catálogo para buscar cuáles de tu lista " +
                    "salen en Argentina, o cargá uno a mano."
                ));

                list.appendChild(empty);

                return;
            }

            if (visible.length === 0) {
                list.appendChild(el("div", "empty",
                    "Ningún manga coincide con esos filtros."));

                return;
            }

            visible.forEach(item => list.appendChild(this.card(item)));

            fillCovers(visible);
        },

        card(item) {
            const card = el("article", "arg-card");

            card.dataset.id = item.mal_id;

            card.appendChild(coverNode(item.image, "arg-cover"));

            const body = el("div", "arg-body");

            const head = el("div", "arg-head");

            head.appendChild(el("h3", "arg-title", item.title));

            if (item.user_status) {
                head.appendChild(el(
                    "span",
                    `tag tag-${item.user_status.replace(/\s+/g, "-").toLowerCase()}`,
                    STATUS_LABELS[item.user_status] || item.user_status
                ));
            }

            body.appendChild(head);

            const meta = el("p", "arg-meta");

            meta.appendChild(el("strong", "publisher", item.publisher || "Editorial sin dato"));

            const volumes = item.volumes_ar
                ? plural(item.volumes_ar, "tomo", "tomos")
                : "tomos sin dato";

            meta.appendChild(el(
                "span", "",
                ` · ${volumes}` +
                (item.ar_status ? ` · ${item.ar_status}` : "")
            ));

            body.appendChild(meta);

            const upcoming = upcomingNode(item);

            if (upcoming) body.appendChild(upcoming);

            const sources = el("p", "arg-sources");

            const labels = {
                manual: "Cargado por vos",
                ivrea: "Catálogo de Ivrea",
                stacks: "Lista de MyAnimeList"
            };

            sources.appendChild(el(
                "span", "",
                (item.sources || []).map(s => labels[s] || s).join(" + ")
            ));

            if (item.url) {
                const link = el("a", "", "Ver en Ivrea ↗");

                link.href = item.url;
                link.target = "_blank";
                link.rel = "noopener noreferrer";

                sources.appendChild(link);
            }

            body.appendChild(sources);

            const actions = el("div", "arg-actions");

            if (item.in_library) {
                const open = el(
                    "a", "btn btn-ghost btn-small",
                    item.volumes_ar
                        ? `En mi biblioteca · ${item.owned_count}/${item.volumes_ar}`
                        : `En mi biblioteca · ${item.owned_count}`
                );

                open.href = "#/biblioteca";

                actions.appendChild(open);

            } else {
                const add = el("button", "btn btn-primary btn-small", "Agregar a mi biblioteca");

                add.type = "button";
                add.addEventListener("click", () => this.addToLibrary(item, add));

                actions.appendChild(add);
            }

            const edit = el("button", "btn btn-ghost btn-small", "Corregir datos");

            edit.type = "button";
            edit.addEventListener("click", () => {
                ManualDialog.open({
                    mal_id: item.mal_id,
                    title: item.title,
                    image: item.image,
                    publisher: item.publisher || "",
                    volumes: item.volumes_ar,
                    status: item.ar_status || ""
                });
            });

            actions.appendChild(edit);

            const isManual = (item.sources || []).includes("manual");

            const remove = el(
                "button", "btn btn-quiet btn-small",
                isManual ? "Quitar de la lista" : "No sale acá"
            );

            remove.type = "button";
            remove.addEventListener("click", () => this.removeItem(item, isManual));

            actions.appendChild(remove);

            body.appendChild(actions);
            card.appendChild(body);

            return card;
        },

        async addToLibrary(item, button) {
            button.disabled = true;

            try {
                await api(`/api/library/${item.mal_id}`, {
                    method: "POST",
                    body: { title: item.title, image: item.image }
                });

                item.in_library = true;
                item.owned_count = 0;

                this.renderList();

                Library.refreshCount();

                toast(`Agregado a Mi biblioteca: ${item.title}`, {
                    action: {
                        label: "Marcar tomos",
                        handler: () => { location.hash = "#/biblioteca"; }
                    }
                });

            } catch (error) {
                button.disabled = false;

                toast(error.message, { error: true });
            }
        },

        async removeItem(item, isManual) {
            try {
                if (isManual) {
                    await api(`/api/argentina/manual/${item.mal_id}`, {
                        method: "DELETE"
                    });
                } else {
                    await api(`/api/argentina/hide/${item.mal_id}`, {
                        method: "POST",
                        body: { hidden: true }
                    });
                }

                await this.enter();

                toast(`Sacado de la lista: ${item.title}`, {
                    action: {
                        label: "Deshacer",
                        handler: () => this.undoRemove(item, isManual)
                    }
                });

            } catch (error) {
                toast(error.message, { error: true });
            }
        },

        async undoRemove(item, wasManual) {
            try {
                if (wasManual) {
                    await api("/api/argentina/manual", {
                        method: "POST",
                        body: {
                            mal_id: item.mal_id,
                            title: item.title,
                            publisher: item.publisher || "Sin dato",
                            volumes_ar: item.volumes_ar,
                            status: item.ar_status,
                            image: item.image
                        }
                    });
                } else {
                    await api(`/api/argentina/hide/${item.mal_id}`, {
                        method: "POST",
                        body: { hidden: false }
                    });
                }

                await this.enter();

            } catch (error) {
                toast(error.message, { error: true });
            }
        },

        // ---- No encontrados

        renderNotFound() {
            const visible = this.notFound.filter(item =>
                !this.notFoundQuery ||
                matchesText(item.title, this.notFoundQuery)
            );

            $("notFoundCount").textContent =
                plural(this.notFound.length, "manga", "mangas") +
                (this.notFound.length ? ". Tocá para verlos." : "");

            $("notFoundSection").classList.toggle(
                "hidden",
                this.notFound.length === 0
            );

            const list = $("notFoundList");

            list.replaceChildren();

            visible.forEach(item => {
                const row = el("li", "nf-row");

                const text = el("div", "nf-text");

                text.appendChild(el("span", "nf-title", item.title));

                if (item.user_status) {
                    text.appendChild(el(
                        "span", "nf-status",
                        STATUS_LABELS[item.user_status] || item.user_status
                    ));
                }

                row.appendChild(text);

                if (item.hidden) {
                    row.appendChild(el("span", "nf-hidden", "Marcado como no publicado"));

                    const back = el("button", "btn btn-quiet btn-small", "Volver a considerar");

                    back.type = "button";

                    back.addEventListener("click", async () => {
                        await api(`/api/argentina/hide/${item.mal_id}`, {
                            method: "POST",
                            body: { hidden: false }
                        });

                        this.enter();
                    });

                    row.appendChild(back);

                } else {
                    const button = el("button", "btn btn-ghost btn-small", "Sale en Argentina");

                    button.type = "button";

                    button.addEventListener("click", () => {
                        ManualDialog.open({
                            mal_id: item.mal_id,
                            title: item.title
                        });
                    });

                    row.appendChild(button);
                }

                list.appendChild(row);
            });
        }
    };


    // ============================================================
    // Diálogo: cargar o corregir un manga
    // ============================================================

    const ManualDialog = {
        chosen: null,

        init() {
            $("manualCancel").addEventListener("click", () => {
                $("manualDialog").close();
            });

            $("manualSearchButton").addEventListener("click", () => this.search());

            $("manualQuery").addEventListener("keydown", event => {
                if (event.key === "Enter") {
                    event.preventDefault();
                    this.search();
                }
            });

            $("manualForm").addEventListener("submit", event => {
                event.preventDefault();
                this.save();
            });
        },

        open(values) {
            this.chosen = values.mal_id
                ? {
                    mal_id: values.mal_id,
                    title: values.title,
                    image: values.image || null
                }
                : null;

            const known = new Set(KNOWN_PUBLISHERS);

            Argentina.items.forEach(item => {
                if (item.publisher) known.add(item.publisher);
            });

            const datalist = $("publisherList");

            datalist.replaceChildren();

            known.forEach(name => {
                const option = el("option");

                option.value = name;

                datalist.appendChild(option);
            });

            $("manualPublisher").value = values.publisher || "";
            $("manualVolumes").value = values.volumes ?? "";
            $("manualStatus").value = values.status || "";
            $("manualQuery").value = "";
            $("manualResults").replaceChildren();

            hideBox($("manualError"));

            this.showChosen();

            $("manualDialog").showModal();

            if (this.chosen) {
                $("manualPublisher").focus();
            } else {
                $("manualQuery").focus();
            }
        },

        showChosen() {
            const searching = !this.chosen;

            $("manualSearchBlock").classList.toggle("hidden", !searching);
            $("manualChosen").classList.toggle("hidden", searching);

            $("manualTitle").textContent = searching
                ? "Agregar un manga a mano"
                : "Sale en Argentina";

            const box = $("manualChosen");

            box.replaceChildren();

            if (this.chosen) {
                box.appendChild(coverNode(this.chosen.image, "chosen-cover"));
                box.appendChild(el("strong", "", this.chosen.title));

                if (!Argentina.notFound.some(m => m.mal_id === this.chosen.mal_id) &&
                    !Argentina.items.some(m => m.mal_id === this.chosen.mal_id)) {

                    const change = el("button", "btn btn-quiet btn-small", "Elegir otro");

                    change.type = "button";

                    change.addEventListener("click", () => {
                        this.chosen = null;
                        this.showChosen();
                    });

                    box.appendChild(change);
                }
            }
        },

        async search() {
            const query = $("manualQuery").value.trim();

            hideBox($("manualError"));

            if (query.length < 2) {
                showBox($("manualError"), "Escribí al menos 2 letras.");
                return;
            }

            const list = $("manualResults");

            list.replaceChildren(el("li", "search-note", "Buscando..."));

            try {
                const data = await api(
                    `/api/argentina/search?q=${encodeURIComponent(query)}`
                );

                list.replaceChildren();

                if (data.results.length === 0) {
                    list.appendChild(el("li", "search-note", "No encontré nada con ese nombre."));
                    return;
                }

                data.results.forEach(result => {
                    const row = el("li", "search-row");

                    row.appendChild(coverNode(result.image, "search-cover"));

                    const text = el("div", "search-text");

                    text.appendChild(el("strong", "", result.title));

                    const detail = [
                        result.type,
                        result.volumes ? `${result.volumes} tomos en Japón` : null
                    ].filter(Boolean).join(" · ");

                    if (detail) text.appendChild(el("span", "", detail));

                    row.appendChild(text);

                    const pick = el("button", "btn btn-ghost btn-small", "Elegir");

                    pick.type = "button";

                    pick.addEventListener("click", () => {
                        this.chosen = {
                            mal_id: result.mal_id,
                            title: result.title,
                            image: result.image
                        };

                        this.showChosen();

                        $("manualPublisher").focus();
                    });

                    row.appendChild(pick);

                    list.appendChild(row);
                });

            } catch (error) {
                list.replaceChildren();

                showBox($("manualError"), error.message);
            }
        },

        async save() {
            hideBox($("manualError"));

            const publisher = $("manualPublisher").value.trim();

            if (!this.chosen) {
                showBox($("manualError"), "Elegí primero el manga.");
                return;
            }

            if (!publisher) {
                showBox($("manualError"), "Indicá la editorial argentina.");
                return;
            }

            const volumesText = $("manualVolumes").value.trim();

            if (volumesText !== "" && !/^\d+$/.test(volumesText)) {
                showBox($("manualError"), "Los tomos tienen que ser un número entero.");
                return;
            }

            $("manualSave").disabled = true;

            try {
                await api("/api/argentina/manual", {
                    method: "POST",
                    body: {
                        mal_id: this.chosen.mal_id,
                        title: this.chosen.title,
                        image: this.chosen.image,
                        publisher,
                        volumes_ar: volumesText === "" ? null : Number(volumesText),
                        status: $("manualStatus").value
                    }
                });

                $("manualDialog").close();

                toast(`Guardado: ${this.chosen.title}`);

                if (currentView() === "argentina") Argentina.enter();

            } catch (error) {
                showBox($("manualError"), error.message);

            } finally {
                $("manualSave").disabled = false;
            }
        }
    };


    // ============================================================
    // MI BIBLIOTECA
    // ============================================================

    const Library = {
        items: [],
        stats: { series: 0, volumes: 0, special: 0 },
        filter: "all",
        query: "",
        saveTimers: {},
        loaded: false,

        init() {
            $("libSearch").addEventListener("input", event => {
                this.query = event.target.value.trim().toLowerCase();
                this.render();
            });

            this.refreshCount();
        },

        async refreshCount() {
            try {
                const data = await api("/api/library");

                this.setCount(data.stats.series);

            } catch {
                /* el contador es opcional */
            }
        },

        setCount(series) {
            const badge = $("navLibraryCount");

            badge.textContent = series;
            badge.classList.toggle("hidden", series === 0);
        },

        async enter() {
            hideBox($("libError"));

            try {
                const data = await api("/api/library");

                this.items = data.items;
                this.stats = data.stats;
                this.loaded = true;

                this.setCount(data.stats.series);

                this.render();

            } catch (error) {
                showBox($("libError"), error.message);
            }
        },

        // El total que se muestra: el que cargó el usuario, o el publicado.
        totalOf(item) {
            // Los tomos publicados se actualizan solos; el número cargado a
            // mano solo cuenta si es mayor (series que Ivrea no informa).
            return Math.max(item.total || 0, item.published || 0);
        },

        shown(item) {
            const total = this.totalOf(item);
            const highest = item.owned.length ? Math.max(...item.owned) : 0;

            return Math.max(total, highest) || 12;
        },

        recomputeStats() {
            this.stats = {
                series: this.items.length,
                volumes: this.items.reduce((n, i) => n + i.owned.length, 0),
                special: this.items.reduce((n, i) => n + i.special.length, 0)
            };

            this.setCount(this.stats.series);
        },

        renderStats() {
            const list = $("libStats");

            list.replaceChildren();

            [
                ["Series", this.stats.series],
                ["Tomos", this.stats.volumes],
                ["Ediciones especiales", this.stats.special]
            ].forEach(([label, value]) => {
                const group = el("div", "stat");

                group.appendChild(el("dd", "stat-value", String(value)));
                group.appendChild(el("dt", "stat-label", label));

                list.appendChild(group);
            });
        },

        isComplete(item) {
            const total = this.totalOf(item);

            return total > 0 && item.owned.length >= total;
        },

        render() {
            this.renderStats();

            const counts = {
                all: this.items.length,
                incomplete: this.items.filter(i => !this.isComplete(i)).length,
                complete: this.items.filter(i => this.isComplete(i)).length,
                special: this.items.filter(i => i.special.length > 0).length
            };

            chipRow(
                $("libFilter"),
                [
                    { value: "all", label: "Todas", count: counts.all },
                    { value: "incomplete", label: "Me faltan tomos", count: counts.incomplete },
                    { value: "complete", label: "Completas", count: counts.complete },
                    { value: "special", label: "Con edición especial", count: counts.special }
                ],
                this.filter,
                value => {
                    this.filter = value;
                    this.render();
                }
            );

            const visible = this.items.filter(item => {
                if (this.query && !matchesText(item.title, this.query)) return false;

                if (this.filter === "incomplete") return !this.isComplete(item);
                if (this.filter === "complete") return this.isComplete(item);
                if (this.filter === "special") return item.special.length > 0;

                return true;
            });

            const list = $("shelfList");

            list.replaceChildren();

            if (this.items.length === 0) {
                const empty = el("div", "empty");

                empty.appendChild(el("strong", "", "Tu biblioteca está vacía"));

                empty.appendChild(el(
                    "p", "",
                    "Agregá series desde En Argentina y marcá acá los " +
                    "tomos que tenés en papel."
                ));

                const go = el("a", "btn btn-primary", "Ir a En Argentina");

                go.href = "#/argentina";

                empty.appendChild(go);

                list.appendChild(empty);

                return;
            }

            if (visible.length === 0) {
                list.appendChild(el("div", "empty", "Ninguna serie coincide con esos filtros."));
                return;
            }

            visible.forEach(item => list.appendChild(this.shelf(item)));

            fillCovers(visible);
        },

        // ---- Una serie y su estante de tomos

        shelf(item) {
            const card = el("article", "shelf-card");

            card.dataset.id = item.mal_id;

            const head = el("div", "shelf-head");

            head.appendChild(coverNode(item.image, "shelf-cover"));

            const info = el("div", "shelf-info");

            info.appendChild(el("h3", "shelf-title", item.title || `Manga ${item.mal_id}`));

            const meta = [
                item.publisher,
                item.published ? `${item.published} tomos publicados` : null,
                item.ar_status
            ].filter(Boolean).join(" · ");

            if (meta) info.appendChild(el("p", "shelf-meta", meta));

            const total = this.totalOf(item);

            const progress = el("p", "shelf-progress");

            progress.appendChild(el(
                "strong", "",
                total ? `${item.owned.length} de ${total}` : `${item.owned.length}`
            ));

            progress.appendChild(el(
                "span", "",
                ` tomos` +
                (item.special.length
                    ? ` · ${plural(item.special.length, "edición especial", "ediciones especiales")}`
                    : "")
            ));

            info.appendChild(progress);

            if (total) {
                const track = el("div", "meter");
                const fill = el("div", "meter-fill");

                fill.style.width = `${Math.min(100, (item.owned.length / total) * 100)}%`;

                track.appendChild(fill);
                info.appendChild(track);
            }

            head.appendChild(info);
            card.appendChild(head);

            card.appendChild(this.spines(item));
            card.appendChild(this.footer(item));

            return card;
        },

        spines(item) {
            const row = el("div", "spines");

            row.style.setProperty("--hue", String(190 + (item.mal_id * 47) % 150));

            const count = this.shown(item);

            for (let volume = 1; volume <= count; volume++) {
                const special = item.special.includes(volume);
                const owned = item.owned.includes(volume);

                const spine = el(
                    "button",
                    "spine" + (special ? " special" : owned ? " owned" : "")
                );

                spine.type = "button";

                const state = special
                    ? "edición especial"
                    : owned ? "lo tengo" : "me falta";

                spine.setAttribute("aria-label", `Tomo ${volume}: ${state}`);
                spine.title = `Tomo ${volume}: ${state}`;

                spine.appendChild(el("span", "spine-num", String(volume)));

                spine.addEventListener("click", () => this.cycle(item, volume));

                row.appendChild(spine);
            }

            if (!this.totalOf(item)) {
                const more = el("button", "spine spine-add", "+");

                more.type = "button";
                more.title = "Agregar un tomo más";
                more.setAttribute("aria-label", "Agregar un tomo más");

                more.addEventListener("click", () => {
                    item.total = count + 1;

                    this.rerender(item);
                    this.save(item, { total: item.total });
                });

                row.appendChild(more);
            }

            return row;
        },

        footer(item) {
            const foot = el("div", "shelf-foot");

            const field = el("label", "shelf-total");

            field.appendChild(el("span", "", "Tomos publicados"));

            const input = el("input", "field field-small");

            input.type = "number";
            input.min = "1";
            input.max = "2000";
            input.inputMode = "numeric";
            const manual = item.total && item.total > (item.published || 0);

            input.value = manual ? item.total : "";
            input.title =
                "Solo hace falta si Tomo no sabe cuántos tomos salieron. " +
                "Si Ivrea informa más, se usa ese número.";
            input.placeholder = item.published ? String(item.published) : "sin dato";

            input.addEventListener("change", () => {
                const text = input.value.trim();

                if (text !== "" && !/^\d+$/.test(text)) {
                    toast("Los tomos tienen que ser un número entero.", { error: true });
                    input.value = manual ? item.total : "";
                    return;
                }

                item.total = text === "" ? null : Number(text);

                this.rerender(item);
                this.save(item, { total: item.total });
            });

            field.appendChild(input);
            foot.appendChild(field);

            const buttons = el("div", "shelf-buttons");

            const all = el("button", "btn btn-ghost btn-small", "Tengo todos");

            all.type = "button";

            all.addEventListener("click", () => {
                const count = this.shown(item);

                item.owned = Array.from({ length: count }, (_, i) => i + 1);

                this.rerender(item);
                this.save(item);
            });

            const remove = el("button", "btn btn-quiet btn-small", "Quitar serie");

            remove.type = "button";

            remove.addEventListener("click", () => this.remove(item));

            buttons.appendChild(all);
            buttons.appendChild(remove);

            foot.appendChild(buttons);

            return foot;
        },

        // sin marcar -> lo tengo -> edición especial -> sin marcar
        cycle(item, volume) {
            const owned = new Set(item.owned);
            const special = new Set(item.special);

            if (special.has(volume)) {
                owned.delete(volume);
                special.delete(volume);

            } else if (owned.has(volume)) {
                special.add(volume);

            } else {
                owned.add(volume);
            }

            item.owned = [...owned].sort((a, b) => a - b);
            item.special = [...special].sort((a, b) => a - b);

            this.rerender(item);
            this.save(item);
        },

        // Redibuja solo la tarjeta de esa serie y las cifras generales.
        rerender(item) {
            this.recomputeStats();
            this.renderStats();

            const old = document.querySelector(
                `.shelf-card[data-id="${item.mal_id}"]`
            );

            if (old) old.replaceWith(this.shelf(item));
        },

        // Guarda con una pequeña espera, para juntar varios toques seguidos.
        save(item, extra = {}) {
            clearTimeout(this.saveTimers[item.mal_id]);

            this.saveTimers[item.mal_id] = setTimeout(async () => {
                try {
                    const body = {
                        owned: item.owned,
                        special: item.special,
                        ...("total" in extra ? { total: item.total } : {})
                    };

                    const data = await api(`/api/library/${item.mal_id}`, {
                        method: "PUT",
                        body
                    });

                    Object.assign(item, {
                        owned: data.item.owned,
                        special: data.item.special,
                        total: data.item.total
                    });

                    hideBox($("libError"));

                } catch (error) {
                    showBox(
                        $("libError"),
                        "No se pudo guardar el cambio: " + error.message
                    );
                }
            }, 300);
        },

        async remove(item) {
            const ok = window.confirm(
                `¿Quitar "${item.title}" de tu biblioteca? ` +
                "Se pierden los tomos que marcaste."
            );

            if (!ok) return;

            try {
                await api(`/api/library/${item.mal_id}`, { method: "DELETE" });

                this.items = this.items.filter(i => i.mal_id !== item.mal_id);

                this.recomputeStats();
                this.render();

                toast(`Quitado: ${item.title}`);

            } catch (error) {
                showBox($("libError"), error.message);
            }
        }
    };


    // --------------------------------
    // Arranque
    // --------------------------------

    Argentina.init();
    ManualDialog.init();
    Library.init();

    window.addEventListener("hashchange", () => {
        showView();
        window.scrollTo(0, 0);
    });

    showView();
})();