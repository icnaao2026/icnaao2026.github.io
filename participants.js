(() => {
    "use strict";

    const form = document.getElementById("participant-filters");
    const search = document.getElementById("participant-search");
    const category = document.getElementById("participant-category");
    const country = document.getElementById("participant-country");
    const count = document.getElementById("participant-result-count");
    const empty = document.getElementById("participant-empty");
    const normalize = value => value.normalize("NFKC").toLocaleLowerCase().trim();
    const people = Array.from(document.querySelectorAll("[data-participant]"), row => ({
        row,
        name: normalize(row.cells[0].textContent),
        category: row.cells[1].textContent.trim(),
        country: row.cells[2].textContent.trim(),
    }));

    function update() {
        const terms = normalize(search.value).split(/\s+/).filter(Boolean);
        let visible = 0;
        for (const person of people) {
            const text = `${person.name} ${normalize(person.country)}`;
            const matches = terms.every(term => text.includes(term))
                && (!category.value || person.category === category.value)
                && (!country.value || person.country === country.value);
            person.row.hidden = !matches;
            if (matches) visible++;
        }
        count.textContent = visible === people.length
            ? `Showing all ${people.length} participants.`
            : `Showing ${visible} of ${people.length} participants.`;
        empty.hidden = visible !== 0;
    }

    form.hidden = false;
    form.addEventListener("submit", event => event.preventDefault());
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    form.addEventListener("reset", () => {
        search.value = "";
        category.value = "";
        country.value = "";
        update();
    });
})();
