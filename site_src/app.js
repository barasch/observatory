(() => {
  const stream = document.querySelector("[data-stream]");
  if (!stream) return;

  const evidence = document.querySelector("#filter-evidence");
  const channel = document.querySelector("#filter-channel");
  const search = document.querySelector("#filter-search");
  const savedOnly = document.querySelector("#filter-saved");
  const count = document.querySelector("[data-visible-count]");
  const storageKey = "observatory:saved:v1";

  const readSaved = () => {
    try {
      return new Set(JSON.parse(localStorage.getItem(storageKey) || "[]"));
    } catch {
      return new Set();
    }
  };

  const writeSaved = (set) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify([...set]));
    } catch {
      // The page remains usable when storage is disabled.
    }
  };

  let saved = readSaved();
  const items = [...stream.querySelectorAll("[data-item]")];

  const updateButtons = () => {
    for (const button of stream.querySelectorAll("[data-save]")) {
      const isSaved = saved.has(button.dataset.save);
      button.setAttribute("aria-pressed", String(isSaved));
      button.textContent = isSaved ? "Saved" : "Save for later";
    }
  };

  const apply = () => {
    const evidenceValue = evidence?.value || "";
    const channelValue = channel?.value || "";
    const query = (search?.value || "").trim().toLocaleLowerCase();
    const onlySaved = savedOnly?.getAttribute("aria-pressed") === "true";
    let visible = 0;
    for (const item of items) {
      const matchesEvidence = !evidenceValue || item.dataset.evidence === evidenceValue;
      const matchesChannel = !channelValue || item.dataset.channel === channelValue;
      const matchesSearch = !query || item.textContent.toLocaleLowerCase().includes(query);
      const matchesSaved = !onlySaved || saved.has(item.dataset.item);
      item.hidden = !(matchesEvidence && matchesChannel && matchesSearch && matchesSaved);
      if (!item.hidden) visible += 1;
    }
    if (count) count.textContent = String(visible);

    for (const heading of stream.querySelectorAll("[data-date-group]")) {
      let sibling = heading.nextElementSibling;
      let hasVisible = false;
      while (sibling && !sibling.hasAttribute("data-date-group")) {
        if (sibling.hasAttribute("data-item") && !sibling.hidden) hasVisible = true;
        sibling = sibling.nextElementSibling;
      }
      heading.hidden = !hasVisible;
    }
  };

  evidence?.addEventListener("change", apply);
  channel?.addEventListener("change", apply);
  search?.addEventListener("input", apply);
  savedOnly?.addEventListener("click", () => {
    const next = savedOnly.getAttribute("aria-pressed") !== "true";
    savedOnly.setAttribute("aria-pressed", String(next));
    savedOnly.textContent = next ? "Showing saved" : "Saved only";
    apply();
  });

  stream.addEventListener("click", (event) => {
    const button = event.target.closest("[data-save]");
    if (!button) return;
    const id = button.dataset.save;
    if (saved.has(id)) saved.delete(id);
    else saved.add(id);
    writeSaved(saved);
    updateButtons();
    apply();
  });

  updateButtons();
  apply();
})();

