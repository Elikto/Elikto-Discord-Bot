"use strict";

const DISCORD_HOSTS = new Set(["discord.com", "www.discord.com"]);
const collator = new Intl.Collator("fr", { numeric: true, sensitivity: "base" });
const state = { data: null, category: "all", search: "", sort: "az" };

const byId = (id) => document.getElementById(id);
const ui = {
  sidebar: byId("sidebar"),
  backdrop: byId("backdrop"),
  menuButton: byId("menuButton"),
  allButton: byId("allButton"),
  allCount: byId("allCount"),
  categoryItems: byId("categoryItems"),
  search: byId("searchInput"),
  sort: byId("sortSelect"),
  title: byId("sectionTitle"),
  caption: byId("sectionCaption"),
  total: byId("resultTotal"),
  grid: byId("resourceGrid"),
  discussionSection: byId("discussionSection"),
  discussionGrid: byId("discussionGrid"),
  empty: byId("emptyState"),
  clear: byId("clearFilters"),
  syncDate: byId("syncDate"),
};

function normalize(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("fr")
    .trim();
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = String(value);
  return node;
}

function discordUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !DISCORD_HOSTS.has(url.hostname)) return "";
    if (!/^\/channels\/\d+\/\d+(?:\/\d+)?\/?$/.test(url.pathname)) return "";
    return url.href;
  } catch {
    return "";
  }
}

function colorFor(value) {
  return /^#[a-fA-F0-9]{6}$/.test(value || "") ? value : "#ae9df8";
}

function categoryList() {
  return [...(state.data?.categories || [])].sort(
    (a, b) => Number(a.rank ?? 100) - Number(b.rank ?? 100) ||
      collator.compare(a.name, b.name)
  );
}

function isDiscussion(category, channel) {
  const categoryName = normalize(category.name);
  const name = normalize(channel.name).replace(/[-_]/g, " ");
  return channel.kind === "text" ||
    categoryName.includes("discussion") ||
    categoryName.includes("salons textuels") ||
    /\b(discussion|general|chat|blabla|bavardage)\b/.test(name);
}

function catalogueEntries() {
  const entries = [];
  const discussion = [];
  const visited = new Set();

  for (const category of categoryList()) {
    if (state.category !== "all" && String(category.id) !== state.category) continue;
    for (const channel of category.channels || []) {
      const conversational = isDiscussion(category, channel);
      const scopeMatch =
        normalize(category.name).includes(normalize(state.search)) ||
        normalize(channel.name).includes(normalize(state.search));

      for (const item of channel.items || []) {
        const href = discordUrl(item.url);
        if (!href || visited.has(href)) continue;
        if (state.search && !scopeMatch &&
            !normalize(item.title).includes(normalize(state.search))) continue;
        visited.add(href);
        const record = { title: item.title, url: href, category, channel };
        (conversational ? discussion : entries).push(record);
      }

      if (conversational) {
        const href = discordUrl(channel.url);
        if (href && (!state.search || scopeMatch)) {
          const id = "salon:" + href;
          if (!visited.has(id)) {
            visited.add(id);
            discussion.push({
              title: channel.name,
              url: href,
              category,
              channel,
              isChannel: true,
            });
          }
        }
      }
    }
  }

  const sortEntries = (a, b) => {
    if (state.sort === "category") {
      return Number(a.category.rank ?? 100) - Number(b.category.rank ?? 100) ||
        collator.compare(a.title, b.title);
    }
    return state.sort === "za" ?
      collator.compare(b.title, a.title) :
      collator.compare(a.title, b.title);
  };
  entries.sort(sortEntries);
  discussion.sort((a, b) => {
    if (state.sort === "category") {
      return Number(a.category.rank ?? 100) - Number(b.category.rank ?? 100) ||
        collator.compare(a.title, b.title);
    }
    return collator.compare(a.title, b.title);
  });
  return { entries, discussion };
}

function closeMenu() {
  ui.sidebar.classList.remove("is-open");
  ui.menuButton.setAttribute("aria-expanded", "false");
  ui.backdrop.hidden = true;
}

function chooseCategory(id) {
  state.category = String(id);
  refreshSidebar();
  render();
  closeMenu();
  if (window.innerWidth < 791) window.scrollTo({ top: 0, behavior: "smooth" });
}

function categoryButton(category) {
  const button = element("button", "nav-item", "");
  const active = state.category === String(category.id);
  if (active) button.classList.add("is-active");
  button.setAttribute("aria-pressed", String(active));
  button.type = "button";
  button.append(
    element("span", "nav-emoji", category.icon || "🧩"),
    element("span", "nav-name", category.name),
    element("span", "nav-count", category.count || (category.channels || []).length)
  );
  button.addEventListener("click", () => chooseCategory(category.id));
  return button;
}

function refreshSidebar() {
  ui.allButton.classList.toggle("is-active", state.category === "all");
  ui.allButton.setAttribute("aria-pressed", String(state.category === "all"));
  ui.allCount.textContent = String(state.data?.stats?.entries ?? 0);
  ui.categoryItems.replaceChildren(...categoryList().map(categoryButton));
}

function resourceCard(item) {
  const link = element("a", "resource-card");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", "Ouvrir " + item.title + " sur Discord");
  link.style.setProperty("--card-accent", colorFor(item.category.color));

  const top = element("div", "card-top");
  top.append(
    element("span", "card-icon", item.channel.icon || item.category.icon || "📁"),
    element("span", "card-arrow", "↗")
  );

  const title = element("h3", "card-title", item.title);
  const details = element("div", "card-details");
  const category = element("span", "card-category", (item.category.icon || "📁") + " " + item.category.name);
  details.append(
    category,
    element("span", "card-divider", "·"),
    element("span", "card-forum", item.channel.name.replace(/[-_]/g, " "))
  );
  const action = element("div", "card-action");
  action.append(element("span", "", "Voir la fiche Discord"), element("span", "", "↗"));
  link.append(top, title, details, action);
  return link;
}

function discussionCard(item) {
  const link = element("a", "discussion-card");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", "Ouvrir " + item.title + " sur Discord");
  link.style.setProperty("--card-accent", colorFor(item.category.color));
  link.append(element("div", "discussion-icon", item.isChannel ? "💬" : "🗨️"));
  const body = element("div", "discussion-body");
  body.append(
    element("strong", "", String(item.title).replace(/[-_]/g, " ")),
    element("span", "", item.category.name + (item.isChannel ? " · Salon" : " · Publication"))
  );
  link.append(body, element("span", "discussion-link-arrow", "↗"));
  return link;
}

function render() {
  if (!state.data) return;
  const selected = categoryList().find((category) => String(category.id) === state.category);
  ui.title.textContent = selected ? selected.name : "Toutes les ressources";
  ui.caption.textContent = selected ? "CATÉGORIE" : "CATALOGUE PUBLIC";

  const { entries, discussion } = catalogueEntries();
  ui.total.textContent = entries.length + " " + (entries.length === 1 ? "fiche" : "fiches");
  ui.grid.replaceChildren(...entries.map(resourceCard));
  ui.grid.hidden = entries.length === 0;

  ui.discussionGrid.replaceChildren(...discussion.map(discussionCard));
  ui.discussionSection.hidden = discussion.length === 0;

  ui.empty.hidden = entries.length > 0 || discussion.length > 0;
}

function resetFilters() {
  state.category = "all";
  state.search = "";
  state.sort = "az";
  ui.search.value = "";
  ui.sort.value = "az";
  refreshSidebar();
  render();
}

function formatDate(value) {
  const date = new Date(value);
  if (!value || !Number.isFinite(date.getTime())) return "Date de synchronisation inconnue";
  return "Mis à jour le " + new Intl.DateTimeFormat("fr-FR", {
    timeZone: "Europe/Paris", day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit"
  }).format(date);
}

function bindEvents() {
  ui.allButton.addEventListener("click", () => chooseCategory("all"));
  ui.search.addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    render();
  });
  ui.sort.addEventListener("change", (event) => {
    state.sort = event.target.value;
    render();
  });
  ui.clear.addEventListener("click", resetFilters);
  ui.menuButton.addEventListener("click", () => {
    const open = !ui.sidebar.classList.contains("is-open");
    ui.sidebar.classList.toggle("is-open", open);
    ui.backdrop.hidden = !open;
    ui.menuButton.setAttribute("aria-expanded", String(open));
  });
  ui.backdrop.addEventListener("click", closeMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
    if (event.key === "/" && !event.ctrlKey && !event.metaKey &&
      !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      ui.search.focus();
    }
  });
}

async function start() {
  bindEvents();
  try {
    const response = await fetch("./catalogue.json", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (!Array.isArray(data.categories) || !data.stats) throw new Error("JSON invalide");
    state.data = data;
    ui.syncDate.textContent = formatDate(data.updated_at);
    refreshSidebar();
    render();
  } catch (error) {
    ui.grid.replaceChildren();
    const errorMessage = element("div", "loading-state", "Le catalogue ne peut pas être chargé. Réessaie plus tard.");
    ui.grid.append(errorMessage);
    console.error("Impossible de charger les fiches publiques Discord :", error);
  }
}

start();
