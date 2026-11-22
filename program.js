(() => {
    "use strict";

    const modes = document.getElementById("program-modes");
    const full = document.getElementById("program-full");
    const interactive = document.getElementById("program-interactive");
    const form = document.getElementById("program-filters");
    const search = document.getElementById("program-search");
    const day = document.getElementById("program-day");
    const room = document.getElementById("program-room");
    const count = document.getElementById("program-result-count");
    const empty = document.getElementById("program-empty");
    const results = document.getElementById("program-results");
    const normalize = value => value.normalize("NFKD").replace(/\p{M}/gu, "").toLocaleLowerCase().trim();
    let mode = "full";

    const fragment = document.createDocumentFragment();
    const talks = Array.from(full.querySelectorAll("[data-program-talk]"), original => {
        const row = original.cloneNode(true);
        row.removeAttribute("id");
        fragment.append(row);
        const data = original.dataset;
        return {
            row,
            date: data.date,
            room: data.room,
            text: normalize([data.session, data.code, data.abstract, data.title, data.presenter].join(" ")),
        };
    });
    results.append(fragment);

    function updateUrl() {
        const url = new URL(window.location.href);
        const hasFilters = search.value || day.value || room.value;
        for (const [key, value] of Object.entries({ mode: mode === "interactive" || hasFilters ? mode : "", q: search.value, day: day.value, room: room.value })) {
            if (value) url.searchParams.set(key, value);
            else url.searchParams.delete(key);
        }
        if (mode === "interactive") url.hash = "";
        // Local file previews may restrict history changes.
        try { window.history.replaceState(null, "", url); } catch { /* The filters still work. */ }
    }

    function update() {
        const terms = normalize(search.value).split(/\s+/).filter(Boolean);
        let visible = 0;
        for (const talk of talks) {
            const matches = terms.every(term => talk.text.includes(term))
                && (!day.value || talk.date === day.value)
                && (!room.value || talk.room === room.value);
            talk.row.hidden = !matches;
            if (matches) visible++;
        }
        count.textContent = visible === talks.length
            ? `Showing all ${talks.length} talks.`
            : `Showing ${visible} of ${talks.length} talks.`;
        empty.hidden = visible !== 0;
        updateUrl();
    }

    function setMode(next) {
        mode = next;
        full.hidden = mode !== "full";
        interactive.hidden = mode !== "interactive";
        for (const button of modes.querySelectorAll("[data-mode]")) {
            button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
        }
        updateUrl();
    }

    function readUrl() {
        const params = new URLSearchParams(window.location.search);
        search.value = params.get("q") || "";
        day.value = params.get("day") || "";
        room.value = params.get("room") || "";
        const next = params.get("mode") === "interactive" || (!params.has("mode") && (search.value || day.value || room.value)) ? "interactive" : "full";
        setMode(next);
        update();
    }

    modes.addEventListener("click", event => {
        const button = event.target.closest("[data-mode]");
        if (button) setMode(button.dataset.mode);
    });
    form.addEventListener("submit", event => event.preventDefault());
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    form.addEventListener("reset", () => {
        search.value = "";
        day.value = "";
        room.value = "";
        update();
    });
    function openLinkedSession() {
        const target = document.getElementById(window.location.hash.slice(1));
        if (!target || !full.contains(target)) return;
        const wasHidden = full.hidden;
        const session = target.closest("details");
        if (session) session.open = true;
        setMode("full");
        if (wasHidden) target.scrollIntoView({ block: "start" });
    }
    window.addEventListener("hashchange", openLinkedSession);
    window.addEventListener("popstate", readUrl);
    full.addEventListener("click", event => {
        const link = event.target.closest('a[href^="#session-"]');
        if (link) document.getElementById(link.hash.slice(1)).open = true;
    });
    modes.hidden = false;
    readUrl();
    openLinkedSession();
})();
