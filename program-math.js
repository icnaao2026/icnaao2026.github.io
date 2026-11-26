(() => {
    "use strict";

    // Only typeset an abstract when opened, rather than processing both copies
    // of every abstract in the full and interactive views on page load.
    window.MathJax = {
        tex: {
            inlineMath: [["\\(", "\\)"]],
            displayMath: [["\\[", "\\]"]],
            tags: "none",
        },
        startup: { typeset: false },
        svg: { fontCache: "local" },
    };

    const rendered = new WeakSet();
    const pending = new WeakSet();

    function render(panel) {
        const content = panel.querySelector(".abstract-content");
        if (!panel.open || !content || rendered.has(content) || pending.has(content)) return;
        const math = window.MathJax;
        if (!math.startup?.promise) return;
        pending.add(content);
        math.startup.promise.then(() => math.typesetPromise([content])).then(() => {
            rendered.add(content);
        }).catch(() => {
            // The original readable text and TeX remain available on failure.
            if (!content.querySelector(".abstract-math-status")) {
                const status = document.createElement("p");
                status.className = "abstract-math-status";
                status.setAttribute("role", "status");
                status.textContent = "Mathematical notation could not load. Reload the page to try again.";
                content.prepend(status);
            }
        }).finally(() => pending.delete(content));
    }

    document.addEventListener("toggle", event => {
        if (event.target.matches("details.program-abstract")) render(event.target);
    }, true);
    // A visitor can open an abstract before the CDN script finishes loading.
    window.addEventListener("load", () => {
        document.querySelectorAll("details.program-abstract[open]").forEach(render);
    });
})();
