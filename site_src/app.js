(() => {
  const currentDate = document.querySelector("[data-current-date]");
  if (currentDate) {
    const now = new Date();
    const localDate = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
    ].join("-");
    currentDate.dateTime = localDate;
    currentDate.textContent = new Intl.DateTimeFormat("en-US", {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    }).format(now);
  }

  const stream = document.querySelector("[data-stream]");
  if (!stream) return;

  const channel = document.querySelector("#filter-channel");
  const search = document.querySelector("#filter-search");
  const savedOnly = document.querySelector("#filter-saved");
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
    const channelValue = channel?.value || "";
    const query = (search?.value || "").trim().toLocaleLowerCase();
    const onlySaved = savedOnly?.getAttribute("aria-pressed") === "true";
    for (const item of items) {
      const matchesChannel = !channelValue || item.dataset.channel === channelValue;
      const matchesSearch = !query || item.textContent.toLocaleLowerCase().includes(query);
      const matchesSaved = !onlySaved || saved.has(item.dataset.item);
      item.hidden = !(matchesChannel && matchesSearch && matchesSaved);
    }

    for (const heading of stream.querySelectorAll("[data-date-group]")) {
      let sibling = heading.nextElementSibling;
      let hasVisible = false;
      while (sibling && !sibling.hasAttribute("data-date-group")) {
        if (sibling.hasAttribute("data-item") && !sibling.hidden) hasVisible = true;
        sibling = sibling.nextElementSibling;
      }
      heading.hidden = !hasVisible;
    }

    for (const section of stream.querySelectorAll("[data-category-section]")) {
      const visible = [...section.querySelectorAll("[data-item]")].filter(
        (item) => !item.hidden,
      ).length;
      const counter = section.querySelector("[data-section-count]");
      if (!counter) continue;
      counter.textContent = section.hasAttribute("data-people-section")
        ? `${visible} ${visible === 1 ? "match" : "matches"}`
        : `${visible} ${visible === 1 ? "item" : "items"}`;
    }
  };

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
