// Search form: searchable selects, filters apply on change, visible loading state.
(function () {
  const form = document.getElementById("search-form");
  const results = document.getElementById("results");

  function busy() {
    document.body.classList.add("loading");
    if (results) results.setAttribute("aria-busy", "true");
  }
  // Back/forward cache restores the page as it was when we left: drop the loading state.
  window.addEventListener("pageshow", () => {
    document.body.classList.remove("loading");
    if (results) results.removeAttribute("aria-busy");
  });

  function submit() {
    if (!form) return;
    busy();
    form.requestSubmit ? form.requestSubmit() : form.submit();
  }

  if (window.TomSelect) {
    document.querySelectorAll("select[data-search]").forEach((el) => {
      new TomSelect(el, {
        allowEmptyOption: true,
        maxOptions: null,
        placeholder: el.dataset.placeholder,
        render: { no_results: () => '<div class="no-results">Ничего не найдено</div>' },
        onDropdownOpen() { this.clear(true); },  // type to search right away; restore below if nothing picked
        onDropdownClose() { if (!this.getValue()) this.setValue(this.input.dataset.last || "", true); },
        onInitialize() { this.input.dataset.last = this.getValue(); },
        onChange(v) { this.input.dataset.last = v; },
      });
    });
    document.querySelectorAll("select[data-plain-ts], select[data-plain]").forEach((el) => {
      new TomSelect(el, { controlInput: null, allowEmptyOption: true });
    });
  }

  if (form && form.dataset.auto === "on") {
    // Selects, checkboxes and the sort (outside the form via form="search-form") re-run the search at once.
    document.addEventListener("change", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLSelectElement || t instanceof HTMLInputElement)) return;
      if (t.name === "q" || t.classList.contains("cmp-box") || t.name === "status") return;
      if (t.form !== form) return;
      submit();
    });
  }
  if (form) {
    form.addEventListener("submit", () => {
      busy();
      // Clean, shareable URLs: no empty or default parameters.
      for (const el of form.elements) {
        if (el.name && (el.value === "" || (el.name === "sort" && el.value === "relevance"))) el.disabled = true;
      }
    });
  }
  document.querySelectorAll(".applied a, .chips a, .empty a").forEach((a) => a.addEventListener("click", busy));
})();

// "Данные: N из M" popovers: one open at a time, close on outside click.
document.addEventListener("click", (e) => {
  document.querySelectorAll("details.data-fill[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
});

// Compare tray: selection is a per-browser convenience, so it lives in localStorage.
(function () {
  const KEY = "goulash.compare";
  const MAX = 6;
  const read = () => {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
  };
  const write = (ids) => {
    try { localStorage.setItem(KEY, JSON.stringify(ids)); } catch (e) { /* private mode */ }
  };
  const tray = document.getElementById("tray");
  const count = document.getElementById("tray-count");
  const go = document.getElementById("tray-go");

  function render() {
    const ids = read();
    document.querySelectorAll(".cmp-box").forEach((b) => { b.checked = ids.includes(b.value); });
    tray.hidden = ids.length === 0 || location.pathname === "/compare";
    count.textContent = `Выбрано: ${ids.length}`;
    go.href = `/compare?ids=${ids.join(",")}`;
    go.classList.toggle("disabled", ids.length < 2);
  }

  document.addEventListener("change", (e) => {
    if (!e.target.classList.contains("cmp-box")) return;
    let ids = read().filter((x) => x !== e.target.value);
    if (e.target.checked) {
      if (ids.length >= MAX) { e.target.checked = false; alert(`Можно сравнить до ${MAX} поставщиков`); return; }
      ids.push(e.target.value);
    }
    write(ids);
    render();
  });
  document.getElementById("tray-clear").addEventListener("click", () => { write([]); render(); });
  render();
})();
