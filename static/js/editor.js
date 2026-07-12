(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const site = $("#site-content");
  const launch = $("#admin-launch");
  const loginModal = $("#login-modal");
  const loginForm = $("#login-form");
  const loginError = $("#login-error");
  const toolbar = $("#editor-toolbar");
  const inspector = $("#editor-panel");
  const fieldsWrap = $("#inspector-fields");
  const emptyState = $("#inspector-empty");
  const selectedName = $("#selected-name");
  const themePanel = $("#theme-panel");
  const sectionsPanel = $("#sections-panel");
  const toastEl = $("#editor-toast");

  let csrf = "";
  let authenticated = false;
  let editMode = false;
  let selected = null;
  let history = [];
  let historyIndex = -1;
  let dirty = false;
  let applyingHistory = false;
  let toastTimer = null;

  const fieldMap = {
    content: $("#field-content"),
    link: $("#field-link"),
    image: $("#field-image-url"),
    color: $("#field-color"),
    bg: $("#field-bg"),
    borderColor: $("#field-border-color"),
    fontSize: $("#field-font-size"),
    fontWeight: $("#field-font-weight"),
    align: $("#field-align"),
    lineHeight: $("#field-line-height"),
    letterSpacing: $("#field-letter-spacing"),
    padding: $("#field-padding"),
    margin: $("#field-margin"),
    width: $("#field-width"),
    height: $("#field-height"),
    radius: $("#field-radius"),
    opacity: $("#field-opacity"),
    shadow: $("#field-shadow"),
    animation: $("#field-animation"),
  };

  const themeInputs = $$("[data-theme-var]");
  const themeKeys = themeInputs.map((input) => input.dataset.themeVar);

  function toast(message, error = false) {
    toastEl.textContent = message;
    toastEl.style.borderColor = error ? "rgba(244,63,94,.4)" : "rgba(168,85,247,.35)";
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2600);
  }

  function rgbToHex(value) {
    if (!value) return "#000000";
    if (value.startsWith("#")) return value.slice(0, 7);
    const match = value.match(/\d+(\.\d+)?/g);
    if (!match || match.length < 3) return "#000000";
    return "#" + match.slice(0, 3).map((part) => {
      return Math.max(0, Math.min(255, Math.round(Number(part)))).toString(16).padStart(2, "0");
    }).join("");
  }

  function getTheme() {
    const styles = getComputedStyle(document.documentElement);
    return Object.fromEntries(themeKeys.map((key) => [key, styles.getPropertyValue(key).trim()]));
  }

  function applyTheme(theme = {}) {
    Object.entries(theme).forEach(([key, value]) => {
      if (themeKeys.includes(key)) document.documentElement.style.setProperty(key, value);
    });
    populateThemeInputs();
  }

  function populateThemeInputs() {
    const styles = getComputedStyle(document.documentElement);
    themeInputs.forEach((input) => {
      const value = styles.getPropertyValue(input.dataset.themeVar).trim();
      input.value = input.type === "color" ? rgbToHex(value) : value;
    });
  }

  async function loadSite() {
    try {
      const response = await fetch("/api/site", { cache: "no-store" });
      const data = await response.json();
      if (data.html) site.innerHTML = data.html;
      applyTheme(data.theme || {});
      window.YSL.initSite();
    } catch (error) {
      console.error(error);
      toast("Could not load saved page.", true);
    }
  }

  async function checkSession() {
    const response = await fetch("/api/session", { cache: "no-store" });
    const data = await response.json();
    if (data.authenticated) {
      authenticated = true;
      csrf = data.csrf;
      enterEditor();
    }
  }

  function snapshot() {
    return {
      html: site.innerHTML,
      theme: getTheme(),
    };
  }

  function pushHistory() {
    if (applyingHistory) return;
    const shot = snapshot();
    history = history.slice(0, historyIndex + 1);
    history.push(shot);
    if (history.length > 35) history.shift();
    historyIndex = history.length - 1;
    dirty = true;
  }

  function restoreHistory(index) {
    if (!history[index]) return;
    applyingHistory = true;
    clearSelection();
    site.innerHTML = history[index].html;
    applyTheme(history[index].theme);
    historyIndex = index;
    window.YSL.initSite();
    applyingHistory = false;
    dirty = true;
  }

  function enterEditor() {
    toolbar.hidden = false;
    inspector.hidden = false;
    launch.hidden = true;
    document.body.classList.add("editor-active");
    editMode = true;
    $("#toggle-edit").classList.add("active");
    $("#toggle-edit").textContent = "Editing on";
    if (!history.length) {
      history = [snapshot()];
      historyIndex = 0;
    }
  }

  function leaveEditor() {
    toolbar.hidden = true;
    inspector.hidden = true;
    themePanel.hidden = true;
    sectionsPanel.hidden = true;
    launch.hidden = false;
    document.body.classList.remove("editor-active");
    editMode = false;
    clearSelection();
  }

  function clearSelection() {
    if (selected) selected.classList.remove("editor-selected");
    selected = null;
    fieldsWrap.hidden = true;
    emptyState.hidden = false;
    selectedName.textContent = "Nothing selected";
  }

  function selectElement(element) {
    if (!element || element === site) return;
    if (selected) selected.classList.remove("editor-selected");
    selected = element;
    selected.classList.add("editor-selected");
    fieldsWrap.hidden = false;
    emptyState.hidden = true;
    selectedName.textContent = `${element.tagName.toLowerCase()}${element.id ? "#" + element.id : ""}${element.classList.length ? "." + [...element.classList].filter(c => !c.startsWith("editor-")).slice(0, 2).join(".") : ""}`;
    fillInspector();
  }

  function fillInspector() {
    if (!selected) return;
    const computed = getComputedStyle(selected);
    fieldMap.content.value = selected.innerHTML;
    fieldMap.link.value = selected.tagName === "A" ? selected.getAttribute("href") || "" : "";
    fieldMap.image.value = selected.tagName === "IMG" ? selected.getAttribute("src") || "" : (selected.style.backgroundImage || "").replace(/^url\(["']?|["']?\)$/g, "");
    fieldMap.color.value = rgbToHex(computed.color);
    fieldMap.bg.value = rgbToHex(computed.backgroundColor);
    fieldMap.borderColor.value = rgbToHex(computed.borderColor);
    fieldMap.fontSize.value = selected.style.fontSize || computed.fontSize;
    fieldMap.fontWeight.value = selected.style.fontWeight || computed.fontWeight;
    fieldMap.align.value = selected.style.textAlign || computed.textAlign || "";
    fieldMap.lineHeight.value = selected.style.lineHeight || computed.lineHeight;
    fieldMap.letterSpacing.value = selected.style.letterSpacing || computed.letterSpacing;
    fieldMap.padding.value = selected.style.padding || computed.padding;
    fieldMap.margin.value = selected.style.margin || computed.margin;
    fieldMap.width.value = selected.style.width || "";
    fieldMap.height.value = selected.style.height || "";
    fieldMap.radius.value = selected.style.borderRadius || computed.borderRadius;
    fieldMap.opacity.value = selected.style.opacity || computed.opacity;
    fieldMap.shadow.value = selected.style.boxShadow || "";
    fieldMap.animation.value = selected.style.animation || "";
  }

  function updateSelected(property, value) {
    if (!selected) return;
    selected.style[property] = value;
    dirty = true;
  }

  function commitChange() {
    pushHistory();
    fillInspector();
  }

  site.addEventListener("click", (event) => {
    if (!editMode) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.target.closest("*");
    if (!target || !site.contains(target)) return;
    selectElement(target);
  }, true);

  site.addEventListener("mouseover", (event) => {
    if (!editMode) return;
    const target = event.target.closest("*");
    if (target && site.contains(target) && target !== selected) target.classList.add("editor-hover");
  }, true);

  site.addEventListener("mouseout", (event) => {
    event.target?.classList?.remove("editor-hover");
  }, true);

  fieldMap.content.addEventListener("change", () => {
    if (!selected) return;
    selected.innerHTML = fieldMap.content.value;
    commitChange();
  });

  fieldMap.link.addEventListener("change", () => {
    if (selected?.tagName === "A") {
      selected.setAttribute("href", fieldMap.link.value || "#");
      commitChange();
    }
  });

  fieldMap.image.addEventListener("change", () => {
    if (!selected) return;
    if (selected.tagName === "IMG") selected.src = fieldMap.image.value;
    else selected.style.backgroundImage = fieldMap.image.value ? `url("${fieldMap.image.value}")` : "";
    commitChange();
  });

  const styleBindings = [
    ["color", "color"], ["bg", "backgroundColor"], ["borderColor", "borderColor"],
    ["fontSize", "fontSize"], ["fontWeight", "fontWeight"], ["align", "textAlign"],
    ["lineHeight", "lineHeight"], ["letterSpacing", "letterSpacing"], ["padding", "padding"],
    ["margin", "margin"], ["width", "width"], ["height", "height"], ["radius", "borderRadius"],
    ["opacity", "opacity"], ["shadow", "boxShadow"], ["animation", "animation"],
  ];

  styleBindings.forEach(([field, property]) => {
    fieldMap[field].addEventListener("input", () => updateSelected(property, fieldMap[field].value));
    fieldMap[field].addEventListener("change", commitChange);
  });

  $("#field-image-upload").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file || !selected) return;
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/upload", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: form,
    });
    const data = await response.json();
    if (!response.ok) return toast(data.error || "Upload failed.", true);
    if (selected.tagName === "IMG") selected.src = data.url;
    else selected.style.backgroundImage = `url("${data.url}")`;
    fieldMap.image.value = data.url;
    commitChange();
    toast("Image uploaded.");
  });

  $("#duplicate-element").onclick = () => {
    if (!selected?.parentElement) return;
    selected.insertAdjacentElement("afterend", selected.cloneNode(true));
    commitChange();
  };

  $("#delete-element").onclick = () => {
    if (!selected || !confirm("Delete this element?")) return;
    const parent = selected.parentElement;
    selected.remove();
    clearSelection();
    pushHistory();
    if (parent) selectElement(parent);
  };

  $("#move-element-up").onclick = () => {
    if (!selected?.previousElementSibling) return;
    selected.parentElement.insertBefore(selected, selected.previousElementSibling);
    commitChange();
  };

  $("#move-element-down").onclick = () => {
    if (!selected?.nextElementSibling) return;
    selected.parentElement.insertBefore(selected.nextElementSibling, selected);
    commitChange();
  };

  $("#hide-element").onclick = () => {
    if (!selected) return;
    const hidden = selected.dataset.editorHidden === "true";
    selected.dataset.editorHidden = String(!hidden);
    commitChange();
  };

  $$("[data-add-block]").forEach((button) => {
    button.onclick = () => {
      if (!selected) return;
      const type = button.dataset.addBlock;
      let element;
      if (type === "text") {
        element = document.createElement("p");
        element.textContent = "New editable text";
      } else if (type === "button") {
        element = document.createElement("a");
        element.href = "#";
        element.className = "button primary";
        element.textContent = "New button →";
      } else if (type === "image") {
        element = document.createElement("img");
        element.alt = "Editable image";
        element.src = "https://placehold.co/800x450/2b1238/ffffff?text=YSL";
        element.style.maxWidth = "100%";
        element.style.borderRadius = "18px";
      } else {
        element = document.createElement("div");
        element.className = "editor-new-block";
        element.innerHTML = "<h3>New card</h3><p>Edit this card from the inspector.</p>";
      }
      selected.appendChild(element);
      pushHistory();
      selectElement(element);
    };
  });

  launch.onclick = async () => {
    if (authenticated) enterEditor();
    else loginModal.hidden = false;
  };

  $("[data-close-login]").onclick = () => { loginModal.hidden = true; };
  loginModal.addEventListener("click", (event) => {
    if (event.target === loginModal) loginModal.hidden = true;
  });

  loginForm.onsubmit = async (event) => {
    event.preventDefault();
    loginError.textContent = "";
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("#admin-password").value }),
    });
    const data = await response.json();
    if (!response.ok) {
      loginError.textContent = data.error || "Login failed.";
      return;
    }
    authenticated = true;
    csrf = data.csrf;
    loginModal.hidden = true;
    enterEditor();
    toast("Editor unlocked.");
  };

  $("#toggle-edit").onclick = () => {
    editMode = !editMode;
    $("#toggle-edit").classList.toggle("active", editMode);
    $("#toggle-edit").textContent = editMode ? "Editing on" : "Preview mode";
    document.body.classList.toggle("editor-active", editMode);
    if (!editMode) clearSelection();
  };

  $("#undo-edit").onclick = () => {
    if (historyIndex > 0) restoreHistory(historyIndex - 1);
  };

  $("#redo-edit").onclick = () => {
    if (historyIndex < history.length - 1) restoreHistory(historyIndex + 1);
  };

  $("#save-site").onclick = async () => {
    clearSelection();
    const response = await fetch("/api/site", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
      },
      body: JSON.stringify({ html: site.innerHTML, theme: getTheme() }),
    });
    const data = await response.json();
    if (!response.ok) return toast(data.error || "Save failed.", true);
    dirty = false;
    toast("Website saved.");
  };

  $("#logout-editor").onclick = async () => {
    if (dirty && !confirm("You have unsaved changes. Log out anyway?")) return;
    await fetch("/api/logout", { method: "POST", headers: { "X-CSRF-Token": csrf } });
    authenticated = false;
    csrf = "";
    leaveEditor();
    toast("Logged out.");
  };

  $("#close-inspector").onclick = () => { inspector.hidden = true; };
  $("#open-theme").onclick = () => {
    themePanel.hidden = !themePanel.hidden;
    sectionsPanel.hidden = true;
    populateThemeInputs();
  };
  $("[data-close-theme]").onclick = () => { themePanel.hidden = true; };
  $("#open-sections").onclick = () => {
    sectionsPanel.hidden = !sectionsPanel.hidden;
    themePanel.hidden = true;
    renderSections();
  };
  $("[data-close-sections]").onclick = () => { sectionsPanel.hidden = true; };

  themeInputs.forEach((input) => {
    input.addEventListener("input", () => {
      document.documentElement.style.setProperty(input.dataset.themeVar, input.value);
      dirty = true;
    });
    input.addEventListener("change", pushHistory);
  });

  function renderSections() {
    const list = $("#section-list");
    list.innerHTML = "";
    const sections = $$("[data-section-name]", site);
    sections.forEach((section) => {
      const row = document.createElement("div");
      row.className = "section-item";
      row.innerHTML = `<span>${section.dataset.sectionName}</span><div><button data-up>↑</button><button data-down>↓</button><button data-select>◎</button></div>`;
      $("[data-up]", row).onclick = () => {
        if (section.previousElementSibling) section.parentElement.insertBefore(section, section.previousElementSibling);
        pushHistory(); renderSections();
      };
      $("[data-down]", row).onclick = () => {
        if (section.nextElementSibling) section.parentElement.insertBefore(section.nextElementSibling, section);
        pushHistory(); renderSections();
      };
      $("[data-select]", row).onclick = () => {
        selectElement(section);
        section.scrollIntoView({ behavior: "smooth", block: "center" });
      };
      list.appendChild(row);
    });
  }

  $("#add-section").onclick = () => {
    const main = $("main", site);
    const section = document.createElement("section");
    section.className = "shell about";
    section.dataset.sectionName = "New Section";
    section.innerHTML = `<article class="about-card editor-new-block"><div class="about-number">+</div><div><span>New section</span><h2>Edit this new section.</h2></div><div><p>Click any part to customise it.</p></div></article>`;
    main.appendChild(section);
    pushHistory();
    renderSections();
    selectElement(section);
    section.scrollIntoView({ behavior: "smooth" });
  };

  $("#export-design").onclick = () => {
    const blob = new Blob([JSON.stringify(snapshot(), null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "ysl-design.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  $("#import-design").onchange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (typeof data.html !== "string" || typeof data.theme !== "object") throw new Error();
      site.innerHTML = data.html;
      applyTheme(data.theme);
      window.YSL.initSite();
      clearSelection();
      pushHistory();
      toast("Design imported.");
    } catch {
      toast("Invalid design file.", true);
    }
  };

  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  loadSite().then(checkSession);
})();
