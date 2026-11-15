/*
   Acceso: decide si hay una sesión iniciada.

   - Sin sesión: muestra el formulario de ingreso / registro.
   - Con sesión: muestra la app y recién ahí carga script.js y argentina.js,
     que empiezan a pedir datos apenas se cargan.
*/

(() => {
    "use strict";

    const $ = id => document.getElementById(id);

    const body = document.body;
    const loader = document.currentScript;

    const form = $("authForm");
    const errorBox = $("authError");

    let mode = "login";

    // --------------------------------
    // Pedidos
    // --------------------------------

    // Si el servidor dice que la sesión venció, se vuelve al ingreso.
    const nativeFetch = window.fetch.bind(window);

    window.fetch = async (input, init) => {
        const response = await nativeFetch(input, init);

        const url = typeof input === "string" ? input : input.url;

        if (response.status === 401 && !url.includes("/api/auth/")) {
            location.reload();
        }

        return response;
    };

    async function post(url, data) {
        const response = await nativeFetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data || {})
        });

        let result = {};

        try {
            result = await response.json();
        } catch {
            // se informa abajo
        }

        if (!response.ok || result.ok === false) {
            throw new Error(
                result.error || `Error del servidor (HTTP ${response.status}).`
            );
        }

        return result;
    }

    // --------------------------------
    // Formulario
    // --------------------------------

    function setMode(next) {
        mode = next;

        const registering = next === "register";

        $("tabLogin").classList.toggle("active", !registering);
        $("tabRegister").classList.toggle("active", registering);
        $("tabLogin").setAttribute("aria-selected", String(!registering));
        $("tabRegister").setAttribute("aria-selected", String(registering));

        $("nameRow").classList.toggle("hidden", !registering);
        $("passwordHint").classList.toggle("hidden", !registering);

        $("authPassword").autocomplete =
            registering ? "new-password" : "current-password";

        $("authSubmit").textContent =
            registering ? "Crear cuenta" : "Ingresar";

        showError("");
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.toggle("hidden", !message);
    }

    $("tabLogin").addEventListener("click", () => setMode("login"));
    $("tabRegister").addEventListener("click", () => setMode("register"));

    form.addEventListener("submit", async event => {
        event.preventDefault();

        const email = $("authEmail").value.trim();
        const password = $("authPassword").value;

        if (!email || !password) {
            showError("Completá el email y la contraseña.");
            return;
        }

        const button = $("authSubmit");

        button.disabled = true;
        showError("");

        try {
            await post(
                mode === "register"
                    ? "/api/auth/register"
                    : "/api/auth/login",
                {
                    email,
                    password,
                    name: $("authName").value.trim()
                }
            );

            // Se recarga para arrancar la app limpia, con la sesión ya puesta.
            location.reload();

        } catch (error) {
            showError(error.message);
            button.disabled = false;
        }
    });

    // --------------------------------
    // Menú de usuario
    // --------------------------------

    function showUser(user) {
        const label = user.name || user.email;

        // Lo leen los otros scripts (por ejemplo, para mostrar herramientas de admin).
        window.TomoUser = user;

        $("userInitial").textContent = label.charAt(0).toUpperCase();
        $("userName").textContent = label;
        $("userEmail").textContent = user.email;

        $("userMenu").classList.remove("hidden");
    }

    $("logoutButton").addEventListener("click", async () => {
        try {
            await post("/api/auth/logout");
        } finally {
            location.reload();
        }
    });

    // Cerrar el menú al tocar afuera.
    document.addEventListener("click", event => {
        const menu = $("userMenu");

        if (menu.open && !menu.contains(event.target)) {
            menu.open = false;
        }
    });

    const deleteDialog = $("deleteDialog");

    $("deleteAccountButton").addEventListener("click", () => {
        $("userMenu").open = false;
        $("deletePassword").value = "";
        $("deleteError").classList.add("hidden");

        deleteDialog.showModal();
        $("deletePassword").focus();
    });

    $("deleteCancel").addEventListener("click", () => deleteDialog.close());

    $("deleteForm").addEventListener("submit", async event => {
        event.preventDefault();

        const button = $("deleteConfirm");

        button.disabled = true;
        $("deleteError").classList.add("hidden");

        try {
            await post("/api/auth/delete-account", {
                password: $("deletePassword").value
            });

            location.reload();

        } catch (error) {
            $("deleteError").textContent = error.message;
            $("deleteError").classList.remove("hidden");
            button.disabled = false;
        }
    });

    // --------------------------------
    // Arranque
    // --------------------------------

    function loadScripts(urls) {
        const [first, ...rest] = urls;

        if (!first) return;

        const script = document.createElement("script");

        script.src = first;
        script.onload = () => loadScripts(rest);

        document.body.appendChild(script);
    }

    async function start() {
        let data;

        try {
            const response = await nativeFetch("/api/auth/me");

            data = await response.json();

        } catch {
            body.classList.remove("is-booting");
            body.classList.add("is-guest");

            $("authScreen").classList.remove("hidden");

            showError("No se pudo conectar con el servidor.");

            return;
        }

        body.classList.remove("is-booting");

        if (!data.user) {
            body.classList.add("is-guest");

            $("authScreen").classList.remove("hidden");

            $("legacyNote").classList.toggle("hidden", !data.legacy_unclaimed);

            // Si hay datos de antes, lo más probable es que quiera crear la cuenta.
            if (data.legacy_unclaimed) setMode("register");

            $("authEmail").focus();

            return;
        }

        showUser(data.user);

        loadScripts(
            (loader.dataset.appScripts || "").split("|").filter(Boolean)
        );
    }

    start();
})();
