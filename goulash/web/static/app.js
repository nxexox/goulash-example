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
