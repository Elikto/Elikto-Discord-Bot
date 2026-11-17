"use strict";

const DISCORD_HOSTS = new Set(["discord.com", "www.discord.com"]);
const collator = new Intl.Collator("fr", { numeric: true, sensitivity: "base" });
const state = { data: null, monitoring: null, category: "all", search: "", sort: "az", health: "all" };

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
  health: byId("healthSelect"),
  healthButtons: [...document.querySelectorAll("[data-health]")],
  healthyCount: byId("healthyCount"),
  incidentCount: byId("incidentCount"),
  restrictedCount: byId("restrictedCount"),
  missingCount: byId("missingCount"),
  lastControl: byId("lastControl"),
  monitorBanner: byId("monitorBanner"),
  historyList: byId("historyList"),
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
        const mirrorStatuses = (item.monitor?.mirrors || []).map((x) => x.status);
        const statuses = mirrorStatuses.length ? mirrorStatuses :
          [item.monitor?.status || "missing_url"];
        if (state.health !== "all" &&
            !(state.health === "incidents" &&
              statuses.some((status) => ["offline", "suspect"].includes(status))) &&
            !statuses.includes(state.health)) continue;
        const href = discordUrl(item.url);
        if (!href || visited.has(href)) continue;
        if (state.search && !scopeMatch &&
            !normalize(item.title).includes(normalize(state.search))) continue;
        visited.add(href);
        const record = { title: item.title, url: href, category, channel, monitor: item.monitor || null };
        (conversational ? discussion : entries).push(record);
      }

      if (conversational && state.health === "all") {
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

function websiteUrl(value) {
  try {
    const result = new URL(value);
    if (!["http:", "https:"].includes(result.protocol)) return "";
    if (result.username || result.password) return "";
    if (/^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)/.test(result.hostname)) return "";
    return result.href;
  } catch {
    return "";
  }
}

const healthLabels = {
  online: ["● Répond", "online"],
  suspect: ["● À vérifier", "suspect"],
  offline: ["● Inaccessible", "offline"],
  restricted: ["● Accès restreint", "restricted"],
  pending: ["● En attente", "pending"],
  missing_url: ["● Adresse manquante", "missing"],
  unknown: ["● Indéterminé", "unknown"]
};

function resourceCard(item) {
  const card = element("article", "resource-card");
  const monitor = item.monitor || {};
  const status = monitor.status || "missing_url";
  const mirrors = Array.isArray(monitor.mirrors) ? monitor.mirrors : [];
  card.dataset.status = status;
  card.style.setProperty("--card-accent", colorFor(item.category.color));

  const top = element("div", "card-top");
  top.append(element("span", "card-icon", item.channel.icon || item.category.icon || "📁"));
  const badgeData = healthLabels[status] || healthLabels.unknown;
  const badge = mirrors.length ?
    mirrors.length + " adresses" : badgeData[0];
  top.append(element("span", "health-badge health-" +
    (mirrors.length ? "pending" : badgeData[1]), badge));

  const title = element("h3", "card-title", item.title);
  const details = element("div", "card-details");
  details.append(
    element("span", "card-category", (item.category.icon || "📁") + " " + item.category.name),
    element("span", "card-divider", "·"),
    element("span", "card-forum", item.channel.name.replace(/[-_]/g, " "))
  );

  const metadata = element("div", "card-monitor-meta");
  metadata.append(element("span", "",
    mirrors.length ? "Vérification de chaque adresse tous les jours" :
    (monitor.last_checked ?
      "Contrôlé le " + formatDate(monitor.last_checked).replace("Mis à jour le ", "") :
      "Contrôle non encore effectué")
  ));
  if (!mirrors.length && monitor.http_status !== null && monitor.http_status !== undefined) {
    metadata.append(element("span", "http-code", "HTTP " + monitor.http_status));
  }

  if (!mirrors.length && monitor.redirect_url) {
    const note = element("p", "card-redirect", "↪ Autre domaine détecté — à confirmer");
    const newTarget = websiteUrl(monitor.redirect_url);
    if (newTarget) {
      const link = element("a", "", "Voir la redirection ↗");
      link.href = newTarget;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      note.append(" · ", link);
    }
    metadata.append(note);
  }
  if (!mirrors.length && status === "missing_url") {
    metadata.append(element("p", "card-no-website",
      "Aucune adresse exploitable détectée dans cette fiche Discord."));
  }

  const mirrorsList = element("div", "mirrors-list");
  if (mirrors.length) {
    mirrorsList.append(element("strong", "mirrors-title", "ADRESSES CONTRÔLÉES"));
    for (const mirror of mirrors) {
      const href = websiteUrl(mirror.url);
      if (!href) continue;
      const row = element("a", "mirror-row");
      row.href = href;
      row.target = "_blank";
      row.rel = "noopener noreferrer nofollow";
      const name = element("span", "mirror-name", mirror.name || new URL(href).hostname);
      const health = healthLabels[mirror.status] || healthLabels.unknown;
      const healthNode = element("span", "mirror-status health-" + health[1], health[0]);
      row.append(name, healthNode);
      mirrorsList.append(row);
    }
  }

  const actions = element("div", "card-actions");
  const siteHref = websiteUrl(monitor.site_url);
  if (siteHref && !mirrors.length) {
    const website = element("a", "card-website", "🌐 Ouvrir le site ↗");
    website.href = siteHref;
    website.target = "_blank";
    website.rel = "noopener noreferrer nofollow";
    website.setAttribute("aria-label", "Ouvrir le site référencé pour " + item.title);
    actions.append(website);
  }
  const discord = element("a", "card-discord", "Voir la fiche Discord ↗");
  discord.href = item.url;
  discord.target = "_blank";
  discord.rel = "noopener noreferrer";
  actions.append(discord);
  card.append(top, title, details, metadata);
  if (mirrors.length) card.append(mirrorsList);
  card.append(actions);
  return card;
}

function updateHealthSummary() {
  const info = state.monitoring || {};
  const counts = info.status_counts || {};
  ui.healthyCount.textContent = String(counts.online || 0);
  ui.incidentCount.textContent = String((counts.offline || 0) + (counts.suspect || 0));
  ui.restrictedCount.textContent = String(counts.restricted || 0);
  ui.missingCount.textContent = String(counts.missing_url || 0);
  ui.lastControl.textContent = info.updated_at ?
    "Synthèse du " + formatDate(info.updated_at).replace("Mis à jour le ", "") :
    "Aucun contrôle publié pour le moment";
  const missing = counts.missing_url || 0;
  const incident = (counts.offline || 0) + (counts.suspect || 0);
  let explanation = "";
  if (!state.monitoring) explanation = "Surveillance non initialisée : aucun état n'est encore disponible.";
  else if (missing) explanation = missing + " fiche(s) sans adresse de site détectable. Elles ne sont pas testées automatiquement.";
  else if (incident) explanation = incident + " site(s) nécessitent une vérification. Une erreur temporaire n'est pas une panne confirmée.";
  ui.monitorBanner.hidden = !explanation;
  ui.monitorBanner.textContent = explanation;
}

function renderHistory() {
  const events = (state.monitoring?.events || []).slice(0, 16);
  if (!events.length) {
    ui.historyList.replaceChildren(element("p", "history-empty",
      "Aucun changement détecté pour le moment. Les événements apparaîtront ici après les vérifications."));
    return;
  }
  const labels = {
    new_resource: "🆕 Nouvelle fiche",
    title_changed: "✏️ Nom modifié",
    address_changed: "🔗 Adresse modifiée",
    redirect_detected: "🔀 Redirection détectée",
    site_unreachable: "🔴 Inaccessibilité confirmée",
    site_recovered: "🟢 Site de nouveau joignable",
    mirror_added: "🆕 Nouvelle adresse miroir",
    mirror_removed: "➖ Adresse retirée",
    mirror_renamed: "✏️ Nom d'adresse modifié",
    mirror_redirect: "🔀 Redirection d'un miroir",
    mirror_offline: "🔴 Adresse miroir inaccessible",
    mirror_recovered: "🟢 Adresse miroir rétablie"
  };
  const cards = events.map((entry) => {
    const row = element("article", "history-event");
    const head = element("div", "history-event-head");
    head.append(
      element("strong", "", labels[entry.type] || "🔎 Changement"),
      element("time", "", formatDate(entry.at).replace("Mis à jour le ", ""))
    );
    row.append(
      head,
      element("p", "history-event-title", entry.title || "Fiche"),
      element("p", "history-event-detail", entry.detail || "")
    );
    if (entry.previous || entry.current) {
      row.append(element("div", "history-event-values",
        [entry.previous, entry.current].filter(Boolean).join("  →  ")));
    }
    const linkUrl = discordUrl(entry.discord_url);
    if (linkUrl) {
      const link = element("a", "history-event-link", "Ouvrir la fiche ↗");
      link.href = linkUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link);
    }
    return row;
  });
  ui.historyList.replaceChildren(...cards);
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
  ui.healthButtons.forEach(button => button.classList.toggle("is-selected", state.health === button.dataset.health));
  ui.grid.hidden = entries.length === 0;

  ui.discussionGrid.replaceChildren(...discussion.map(discussionCard));
  ui.discussionSection.hidden = discussion.length === 0;

  ui.empty.hidden = entries.length > 0 || discussion.length > 0;
}

function resetFilters() {
  state.category = "all";
  state.search = "";
  state.sort = "az";
  state.health = "all";
  ui.health.value = "all";
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
  ui.health.addEventListener("change", (event) => {
    state.health = event.target.value;
    render();
  });
  ui.healthButtons.forEach(button => button.addEventListener("click", () => {
    state.health = state.health === button.dataset.health ? "all" : button.dataset.health;
    ui.health.value = state.health;
    render();
  }));
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
    const [response, monitorResponse] = await Promise.all([
      fetch("./catalogue.json", { cache: "no-store" }),
      fetch("./monitoring.json", { cache: "no-store" }).catch(() => null),
    ]);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    state.monitoring = monitorResponse?.ok ? await monitorResponse.json() : null;
    if (!Array.isArray(data.categories) || !data.stats) throw new Error("JSON invalide");
    state.data = data;
    ui.syncDate.textContent = formatDate(data.updated_at);
    updateHealthSummary();
    renderHistory();
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
