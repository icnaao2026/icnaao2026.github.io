(() => {
    "use strict";

    const root = document.documentElement;
    const preference = window.matchMedia("(prefers-color-scheme: dark)");
    const storageKey = "icnaao-theme";
    let savedTheme = null;
    try { savedTheme = window.localStorage.getItem(storageKey); } catch { /* Storage is optional. */ }
    let hasChoice = savedTheme === "light" || savedTheme === "dark";

    function apply(theme) {
        root.dataset.theme = theme;
        const button = document.getElementById("theme-toggle");
        if (!button) return;
        const dark = theme === "dark";
        button.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} mode`);
        button.querySelector("[data-theme-icon]").textContent = dark ? "☀" : "☾";
        button.querySelector("[data-theme-label]").textContent = dark ? "Light mode" : "Dark mode";
        button.hidden = false;
    }

    apply(hasChoice ? savedTheme : (preference.matches ? "dark" : "light"));

    document.addEventListener("DOMContentLoaded", () => {
        apply(root.dataset.theme);
        document.getElementById("theme-toggle").addEventListener("click", () => {
            const theme = root.dataset.theme === "dark" ? "light" : "dark";
            hasChoice = true;
            apply(theme);
            try { window.localStorage.setItem(storageKey, theme); } catch { /* Keep the choice for this page. */ }
        });
    });

    preference.addEventListener("change", event => {
        if (!hasChoice) apply(event.matches ? "dark" : "light");
    });
    window.addEventListener("storage", event => {
        if (event.key !== storageKey && event.key !== null) return;
        hasChoice = event.newValue === "light" || event.newValue === "dark";
        apply(hasChoice ? event.newValue : (preference.matches ? "dark" : "light"));
    });
})();
