const DISCORD_BASE = "https://discord.com/channels/";
const COLLATOR = new Intl.Collator("fr", { sensitivity: "base", numeric: true });

const state = {
  data: null,
  category: "all",
  query: "",
  sort: "default",
  expanded: new Set(),
  expandAll: false,
};

const $ = (selector) => document.querySelector(selector);
const dom = {
  sidebar: $("#sidebar"),
  backdrop: $("#menuBackdrop"),
  mobileMenu: $("#mobileMenu"),
  nav: $("#categoryNav"),
  navAll: $("#allCategories"),
  tabs: $("#categoryTabs"),
  cards: $("#cards"),
  input: $("#search"),
  sort: $("#sort"),
  empty: $("#emptyState"),
  reset: $("#resetFilters"),
  expandAll: $("#expandAll"),
  title: $("#resultsTitle"),
  count: $("#resultCount"),
};

function normalized(input) {
  return String(input || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("fr")
    .trim();
}

function safeUrl(url) {
  try {
    const value = new URL(url);
    if (value.protocol !== "https:") return "";
    if (value.hostname !== "discord.com" && value.hostname !== "www.discord.com") return "";
    if (!/^\/channels\/\d+\/\d+/.test(value.pathname)) return "";
    return value.href;
  } catch {
    return "";
  }
}

function el(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function addLink(parent, text, url, className = "") {
  const target = safeUrl(url);
  if (!target) return null;
  const link = el("a", className, text);
  link.href = target;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  parent.append(link);
  return link;
}

function sortedCategories() {
  return [...(state.data?.categories || [])].sort(
    (a, b) => Number(a.rank ?? 100) - Number(b.rank ?? 100) || COLLATOR.compare(a.name, b.name)
  );
}

function countLabel(count, singular, plural) {
  return String(count) + " " + (count === 1 ? singular : plural);
}

function setCategory(id) {
  state.category = String(id);
  state.expanded.clear();
  state.expandAll = false;
  renderNavigation();
  render();
  closeMenu();
}

function navButton(cat, isAll = false) {
  const id = isAll ? "all" : String(cat.id);
  const btn = el("button", "nav-link" + (state.category === id ? " is-active" : ""));
  btn.type = "button";
  btn.setAttribute("aria-pressed", String(state.category === id));
  const emoji = el("span", "nav-symbol", isAll ? "◈" : cat.icon || "📁");
  const title = el("span", "", isAll ? "Vue d'ensemble" : cat.name);
  const counter = el("span", "nav-count", isAll ? String(state.data.stats.categories) : String(cat.count || 0));
  btn.append(emoji, title, counter);
  btn.addEventListener("click", () => setCategory(id));
  return btn;
}

function categoryPill(cat, all = false) {
  const id = all ? "all" : String(cat.id);
  const btn = el("button", "category-tab" + (state.category === id ? " is-active" : ""));
  btn.type = "button";
  btn.setAttribute("aria-pressed", String(state.category === id));
  btn.append(el("span", "", all ? "✦" : cat.icon || "📁"), el("span", "", all ? "Tout voir" : cat.name));
  btn.addEventListener("click", () => setCategory(id));
  return btn;
}

function renderNavigation() {
  if (!state.data) return;
  const cats = sortedCategories();
  dom.nav.replaceChildren(...cats.map((cat) => navButton(cat)));
  dom.tabs.replaceChildren(categoryPill(null, true), ...cats.map((cat) => categoryPill(cat)));
  dom.navAll.classList.toggle("is-active", state.category === "all");
  dom.navAll.setAttribute("aria-pressed", String(state.category === "all"));
  $("#navTotal").textContent = String(state.data.stats.categories);
}

function formatDate(input) {
  if (!input) return "Date inconnue";
  const d = new Date(input);
  if (Number.isNaN(d.getTime())) return "Date inconnue";
  return new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" }).format(d);
}

function updateStats() {
  const totals = state.data.stats || {};
  $("#statCategories").textContent = String(totals.categories ?? 0);
  $("#statForums").textContent = String(totals.channels ?? 0);
  $("#statEntries").textContent = String(totals.entries ?? 0);
  $("#lastUpdateHero").textContent = "Mis à jour le " + formatDate(state.data.updated_at);
}

function discussionLast(name) {
  const n = normalized(name);
  return n.includes("discussion") || n.includes("blabla") || n === "general" || n === "général" || n === "chat";
}

function filterCards() {
  const query = normalized(state.query);
  const result = [];
  for (const category of sortedCategories()) {
    if (state.category !== "all" && String(category.id) !== state.category) continue;
    for (const channel of category.channels || []) {
      const all = channel.items || [];
      const matchesContext = normalized(category.name).includes(query) || normalized(channel.name).includes(query);
      const filtered = !query || matchesContext ? all : all.filter((item) => normalized(item.title).includes(query));
      if (query && !matchesContext && filtered.length === 0) continue;
      result.push({ category, channel, items: filtered, total: all.length });
    }
  }

  if (state.sort === "az") {
    result.sort((a, b) => Number(discussionLast(a.channel.name)) - Number(discussionLast(b.channel.name)) || COLLATOR.compare(a.channel.name, b.channel.name));
  } else if (state.sort === "za") {
    result.sort((a, b) => Number(discussionLast(a.channel.name)) - Number(discussionLast(b.channel.name)) || COLLATOR.compare(b.channel.name, a.channel.name));
  } else if (state.sort === "size") {
    result.sort((a, b) => Number(discussionLast(a.channel.name)) - Number(discussionLast(b.channel.name)) || b.total - a.total || COLLATOR.compare(a.channel.name, b.channel.name));
  }
  return result;
}

function resultCard(info) {
  const { category, channel, items, total } = info;
  const identity = String(category.id) + ":" + String(channel.name);
  const expanded = state.expandAll || state.expanded.has(identity);
  const visible = expanded ? items : items.slice(0, 4);
  const color = /^#[0-9a-fA-F]{6}$/.test(category.color || "") ? category.color : "#a18aff";

  const card = el("article", "resource-card");
  card.style.setProperty("--card-accent", color);

  const inner = el("div", "card-inner");
  const header = el("div", "card-top");
  header.append(el("span", "category-label", (category.icon || "📁") + "  " + category.name));
  addLink(header, "↗", channel.url, "card-outlink");
  inner.append(header);

  const head = el("div", "card-head");
  head.append(el("div", "card-avatar", channel.icon || "📂"));
  const titles = el("div", "card-title-wrap");
  titles.append(el("h3", "card-title", channel.name.replace(/[-_]/g, " ")));
  titles.append(el("p", "card-subtitle", channel.kind === "forum" ? countLabel(total, "publication", "publications") + " dans ce forum" : "Salon Discord"));
  head.append(titles);
  inner.append(head);

  const entriesTitle = el("div", "entries-title");
  entriesTitle.append(el("span", "", "PUBLICATIONS"), el("span", "entry-count", state.query && items.length !== total ? items.length + " trouvée(s)" : String(total)));
  inner.append(entriesTitle);

  if (visible.length === 0) {
    inner.append(el("p", "card-no-items", state.query ? "Aucune fiche correspondante." : "Ce salon ne contient pas de publication de forum à indexer."));
  } else {
    const list = el("ul", "entries-list");
    for (const item of visible) {
      const li = el("li");
      const link = addLink(li, "", item.url, "entry-link");
      if (!link) continue;
      link.append(el("span", "entry-bullet"), el("span", "entry-name", item.title), el("span", "entry-arrow", "↗"));
      list.append(li);
    }
    inner.append(list);
  }
  card.append(inner);

  const footer = el("div", "card-footer");
  addLink(footer, channel.kind === "forum" ? "Ouvrir le forum ↗" : "Voir le salon ↗", channel.url, "card-forum");
  if (items.length > 4) {
    const btn = el("button", "more-btn", expanded ? "Afficher moins ↑" : "Voir les " + (items.length - 4) + " autres ↓");
    btn.type = "button";
    btn.setAttribute("aria-expanded", String(expanded));
    btn.addEventListener("click", () => {
      if (expanded) {
        state.expanded.delete(identity);
        state.expandAll = false;
      } else {
        state.expanded.add(identity);
      }
      render();
    });
    footer.append(btn);
  }
  card.append(footer);
  return card;
}

function render() {
  if (!state.data) return;
  const items = filterCards();
  const selected = sortedCategories().find((c) => String(c.id) === state.category);
  dom.title.textContent = selected ? selected.icon + " " + selected.name : "Tous les forums";
  dom.count.textContent = String(items.length);
  dom.expandAll.textContent = state.expandAll ? "Tout réduire ↑" : "Tout développer ↓";
  dom.cards.replaceChildren(...items.map((item) => resultCard(item)));
  dom.empty.hidden = items.length > 0;
  dom.cards.hidden = items.length === 0;
}

function closeMenu() {
  dom.sidebar.classList.remove("is-open");
  dom.mobileMenu.setAttribute("aria-expanded", "false");
  dom.backdrop.hidden = true;
}

function resetFilters() {
  state.category = "all";
  state.query = "";
  state.sort = "default";
  state.expanded.clear();
  state.expandAll = false;
  dom.input.value = "";
  dom.sort.value = "default";
  renderNavigation();
  render();
  closeMenu();
}

function bindUi() {
  dom.navAll.addEventListener("click", () => setCategory("all"));
  dom.input.addEventListener("input", (event) => { state.query = event.target.value; render(); });
  dom.sort.addEventListener("change", (event) => { state.sort = event.target.value; render(); });
  dom.expandAll.addEventListener("click", () => { state.expandAll = !state.expandAll; state.expanded.clear(); render(); });
  dom.reset.addEventListener("click", resetFilters);
  dom.mobileMenu.addEventListener("click", () => {
    const open = !dom.sidebar.classList.contains("is-open");
    dom.sidebar.classList.toggle("is-open", open);
    dom.mobileMenu.setAttribute("aria-expanded", String(open));
    dom.backdrop.hidden = !open;
  });
  dom.backdrop.addEventListener("click", closeMenu);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeMenu(); if (document.activeElement === dom.input) dom.input.blur(); return; }
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      dom.input.focus();
    }
  });
}

async function loadCatalogue() {
  bindUi();
  try {
    const response = await fetch("./catalogue.json", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (!Array.isArray(data.categories) || !data.stats) throw new Error("Format de catalogue invalide");
    state.data = data;
    updateStats();
    renderNavigation();
    render();
  } catch (error) {
    dom.cards.replaceChildren();
    const message = el("div", "loading-card");
    message.append(el("div", "", "⚠️"), el("p", "", "Le catalogue est temporairement indisponible."));
    message.append(el("p", "", "Réessaie après la prochaine synchronisation Discord."));
    dom.cards.append(message);
    console.error("Erreur de chargement du catalogue :", error);
  }
}

loadCatalogue();
