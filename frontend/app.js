/**
 * Signalpost Client Application.
 * Communicates with FastAPI backend for research, refresh, and history.
 */
document.addEventListener("DOMContentLoaded", () => {
  const inputEl = document.getElementById("company-input");
  const btnResearch = document.getElementById("btn-research");
  const btnRefresh = document.getElementById("btn-refresh");
  const btnHistory = document.getElementById("btn-history");
  const alertBanner = document.getElementById("alert-banner");
  const loadingState = document.getElementById("loading-state");
  const resultsContainer = document.getElementById("results-container");
  const systemStatus = document.getElementById("system-status");
  const statusText = document.getElementById("status-text");

  // History modal elements
  const historyModal = document.getElementById("history-modal");
  const modalClose = document.getElementById("modal-close");
  const historyRuns = document.getElementById("history-runs");
  const historyChanges = document.getElementById("history-changes");

  let currentCompanyNumber = null;

  // 1. Initial Health Check
  async function checkHealth() {
    try {
      const res = await fetch("/health");
      const data = await res.json();
      if (data.status === "healthy") {
        statusText.textContent = `Online (${data.llm_provider} / ${data.search_provider})`;
        systemStatus.querySelector(".status-dot").style.backgroundColor = "var(--success)";
      } else {
        statusText.textContent = "Degraded";
        systemStatus.querySelector(".status-dot").style.backgroundColor = "var(--warning)";
      }
    } catch {
      statusText.textContent = "Offline";
      systemStatus.querySelector(".status-dot").style.backgroundColor = "var(--danger)";
    }
  }
  checkHealth();

  // 2. Chip Click Handler
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const orgNr = chip.getAttribute("data-org");
      inputEl.value = orgNr;
      startResearch(orgNr);
    });
  });

  // 3. Button Click Handlers
  btnResearch.addEventListener("click", () => {
    const val = cleanOrgNumber(inputEl.value);
    if (!val) {
      showAlert("Please enter a 9-digit Norwegian company number.", "error");
      return;
    }
    startResearch(val);
  });

  btnRefresh.addEventListener("click", () => {
    const val = cleanOrgNumber(inputEl.value) || currentCompanyNumber;
    if (!val) {
      showAlert("Please enter a company number to refresh.", "error");
      return;
    }
    refreshCompany(val);
  });

  btnHistory.addEventListener("click", () => {
    const val = cleanOrgNumber(inputEl.value) || currentCompanyNumber;
    if (!val) {
      showAlert("Please enter a company number to view history.", "error");
      return;
    }
    loadHistory(val);
  });

  modalClose.addEventListener("click", () => {
    historyModal.classList.add("hidden");
  });

  window.addEventListener("click", (e) => {
    if (e.target === historyModal) {
      historyModal.classList.add("hidden");
    }
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      btnResearch.click();
    }
  });

  function cleanOrgNumber(str) {
    return (str || "").replace(/\D/g, "");
  }

  function showAlert(msg, type = "info") {
    alertBanner.textContent = msg;
    alertBanner.className = `alert-banner ${type}`;
    alertBanner.classList.remove("hidden");
  }

  function hideAlert() {
    alertBanner.classList.add("hidden");
  }

  function setLoading(isLoading, title = "Agent Researching Company...") {
    if (isLoading) {
      hideAlert();
      resultsContainer.classList.add("hidden");
      loadingState.classList.remove("hidden");
      document.getElementById("loading-title").textContent = title;
      btnResearch.disabled = true;
      btnRefresh.disabled = true;
    } else {
      loadingState.classList.add("hidden");
      btnResearch.disabled = false;
      btnRefresh.disabled = false;
    }
  }

  // 4. API Actions
  async function startResearch(orgNr) {
    setLoading(true, `Agent Researching ${orgNr}...`);
    try {
      const res = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company_number: orgNr })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to research company.");
      }
      currentCompanyNumber = data.company_number;
      renderProfile(data);
    } catch (err) {
      showAlert(err.message, "error");
    } finally {
      setLoading(false);
    }
  }

  async function refreshCompany(orgNr) {
    setLoading(true, `Refreshing ${orgNr} and verifying data freshness...`);
    try {
      const res = await fetch(`/api/company/${orgNr}/refresh`, {
        method: "POST"
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to refresh company.");
      }
      currentCompanyNumber = data.profile.company_number;
      renderProfile(data.profile);
      const changesCount = data.changes.filter(c => c.change_type !== "verified_unchanged").length;
      showAlert(`Refresh complete. ${changesCount} field updates detected. ${data.changes.length} total facts audited.`, "info");
    } catch (err) {
      showAlert(err.message, "error");
    } finally {
      setLoading(false);
    }
  }

  async function loadHistory(orgNr) {
    try {
      const res = await fetch(`/api/company/${orgNr}/history`);
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Could not fetch company history.");
      }
      renderHistoryModal(data);
      historyModal.classList.remove("hidden");
    } catch (err) {
      showAlert(err.message, "error");
    }
  }

  // 5. Render Profile
  function renderProfile(profile) {
    const id = profile.identity;
    document.getElementById("company-name").textContent = id.name || "Unknown Name";
    document.getElementById("company-org-nr").textContent = id.company_number || profile.company_number || "Not reported";
    document.getElementById("company-founded").textContent = id.founding_date || "Not reported";
    document.getElementById("company-registered").textContent = id.registration_date || "Not reported";

    const badgeStatus = document.getElementById("badge-status");
    badgeStatus.textContent = id.status || "Status not reported";
    badgeStatus.className = `badge ${id.status === "Active" ? "badge-active" : "badge-neutral"}`;
    document.getElementById("badge-org-type").textContent = id.organization_type_code || id.organization_type || "Type not reported";
    document.getElementById("badge-country").textContent = id.country || "Country not reported";

    const confidence = Number.isFinite(profile.overall_confidence) ? profile.overall_confidence : null;
    const confVal = confidence === null ? null : Math.round(confidence * 100);
    const confEl = document.getElementById("confidence-val");
    confEl.textContent = confVal === null ? "Not reported" : `${confVal}%`;
    document.getElementById("summary-confidence").textContent = confVal === null
      ? "Not reported"
      : `${confVal}% evidence-backed`;
    document.getElementById("company-address").textContent = id.registered_address || "Not reported";
    document.getElementById("company-city").textContent = [id.postal_code, id.city, id.country]
      .filter(Boolean).join(" ") || "Not reported";

    const websiteEl = document.getElementById("company-website");
    const siteUrl = profile.website || id.official_website;
    const safeWebsite = safeHttpUrl(siteUrl);
    if (safeWebsite) {
      websiteEl.innerHTML = `<a href="${escapeHtml(safeWebsite)}" target="_blank" rel="noopener noreferrer" class="src-link">${escapeHtml(siteUrl)} ↗</a>`;
    } else {
      websiteEl.textContent = "Not reported";
    }

    document.getElementById("company-employees").textContent = profile.employee_count !== null && profile.employee_count !== undefined
      ? `${Number(profile.employee_count).toLocaleString()} registered`
      : "Not reported";

    const industryText = [
      profile.industry_code,
      profile.industry_description,
      profile.industry_focus
    ].filter(Boolean).join(" · ") || "Not reported";
    document.getElementById("fact-industry").textContent = industryText;
    document.getElementById("fact-purpose").textContent = profile.business_purpose || "Not reported";
    document.getElementById("company-summary").textContent = profile.company_summary || "Not reported";
    document.getElementById("summary-industry").textContent =
      profile.industry_focus || profile.industry_description || "Not reported";
    document.getElementById("summary-headquarters").textContent =
      profile.headquarters_city || id.city || "Not reported";

    const management = Array.isArray(profile.management) ? profile.management : [];
    const mentions = Array.isArray(profile.leadership_mentions) ? profile.leadership_mentions : [];
    const ceoConflict = (profile.conflicts || []).find((conflict) =>
      ["ceo", "chief_executive", "chief_executive_officer"].includes(String(conflict.field).toLowerCase())
    );
    const registryCeo = management.find((person) => /\b(ceo|daglig leder|chief executive)\b/i.test(person.role));
    const citedCeo = (profile.evidence_list || []).find((ev) =>
      ["ceo", "chief_executive", "chief_executive_officer", "daglig_leder"].includes(String(ev.field).toLowerCase())
    );
    const mentionCeo = mentions.find((name) => /\b(ceo|chief executive|daglig leder)\b/i.test(name));
    const ceoValue = ceoConflict?.primary_value || registryCeo?.name ||
      citedCeo?.value || mentionCeo || "Not reported";
    document.getElementById("summary-ceo").textContent = String(ceoValue);

    const people = [];
    if (ceoValue !== "Not reported") people.push({ role: "Chief executive", name: String(ceoValue) });
    const chair = management.find((person) => /\b(chair|styreleder)\b/i.test(person.role));
    if (chair && !people.some((person) => person.name.toLowerCase() === chair.name.toLowerCase())) {
      people.push({ role: "Board chair", name: chair.name });
    }
    const keyPeople = document.getElementById("key-people");
    keyPeople.innerHTML = people.length
      ? people.map((person) => `<article class="person-card"><span>${escapeHtml(person.role)}</span><strong>${escapeHtml(person.name)}</strong></article>`).join("")
      : '<p class="empty-note">Leadership details not reported.</p>';

    const featuredNames = new Set(people.map((person) => person.name.toLowerCase()));
    const remainingPeople = management
      .filter((person) => !featuredNames.has(person.name.toLowerCase()))
      .map((person) => ({ role: person.role, name: person.name }));
    mentions.forEach((name) => {
      const matchedPerson = remainingPeople.some((person) => person.name.toLowerCase() === String(name).toLowerCase());
      const featured = featuredNames.has(String(name).toLowerCase());
      if (!matchedPerson && !featured) remainingPeople.push({ role: "Public mention", name: String(name) });
    });
    const otherPeopleDetails = document.getElementById("other-people-details");
    const managementList = document.getElementById("management-list");
    if (remainingPeople.length) {
      otherPeopleDetails.classList.remove("hidden");
      document.getElementById("other-people-summary").textContent = `Other leadership · ${remainingPeople.length}`;
      managementList.innerHTML = remainingPeople.map((person) =>
        `<div class="person-row"><span>${escapeHtml(person.role)}</span><strong>${escapeHtml(person.name)}</strong></div>`
      ).join("");
    } else {
      otherPeopleDetails.classList.add("hidden");
      managementList.innerHTML = "";
    }

    const products = Array.isArray(profile.key_products_or_services)
      ? profile.key_products_or_services.filter(Boolean)
      : [];
    const productsSection = document.getElementById("products-section");
    productsSection.classList.toggle("hidden", products.length === 0);
    document.getElementById("products-list").innerHTML = products.map((product) =>
      `<li>${escapeHtml(product)}</li>`
    ).join("");

    const conflicts = Array.isArray(profile.conflicts) ? profile.conflicts : [];
    const conflictsSection = document.getElementById("conflicts-section");
    const conflictsList = document.getElementById("conflicts-list");
    conflictsList.innerHTML = "";
    conflictsSection.classList.toggle("has-conflicts", conflicts.length > 0);
    document.getElementById("trust-status").textContent = conflicts.length
      ? "Information discrepancy"
      : "✓ No conflicting claims detected";
    conflicts.forEach((conflict) => {
      const item = document.createElement("article");
      item.className = "conflict-item";
      const label = conflict.field === "ceo" ? "CEO" : formatLabel(conflict.field);
      const alternatives = (conflict.alternatives || [conflict.conflicting_value])
        .filter((value) => formatValue(value) !== formatValue(conflict.primary_value));
      const evidence = Array.isArray(conflict.evidence) ? conflict.evidence : [];
      item.innerHTML = `
        <div class="conflict-heading">${escapeHtml(label)}</div>
        <p class="conflict-source-count">${evidence.length} supporting source${evidence.length === 1 ? "" : "s"}</p>
        <p><span>Preferred</span><strong>${escapeHtml(formatValue(conflict.primary_value))}</strong></p>
        ${alternatives.map((value) => `<p><span>Alternative</span><strong>${escapeHtml(formatValue(value))}</strong></p>`).join("")}
        <p class="conflict-explanation">Different sources report different values. The preferred value is based on the strongest available evidence.</p>
        ${evidence.length ? `<details class="conflict-sources"><summary>Supporting sources · ${evidence.length}</summary><ul>${evidence.map((ev) => {
          const href = safeHttpUrl(ev.source_url);
          const sourceName = ev.source_title || ev.source_url || "Source";
          return `<li>${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(sourceName)}</a>` : escapeHtml(sourceName)} · ${Math.round((ev.confidence || 0) * 100)}%<blockquote>${escapeHtml(ev.explanation || "No supporting quote available.")}</blockquote></li>`;
        }).join("")}</ul></details>` : ""}
      `;
      conflictsList.appendChild(item);
    });

    const activity = profile.recent_activity;
    const activitySection = document.getElementById("activity-section");
    const evidenceList = profile.evidence_list || [];
    const activityEvidence = evidenceList
      .filter((ev) => String(ev.field).toLowerCase() === "recent_activity" && ev.publication_date)
      .sort((left, right) => new Date(right.publication_date) - new Date(left.publication_date));
    const matchingActivityEvidence = activityEvidence.find((ev) =>
      activity && (String(activity).includes(String(ev.value)) || String(ev.value).includes(String(activity)))
    );
    const noRecentActivity = !activity ||
      /^No recent activity found/i.test(String(activity));
    activitySection.classList.remove("hidden");
    const activityList = document.getElementById("activity-list");
    const activityEmpty = document.getElementById("activity-empty");
    if (noRecentActivity || !matchingActivityEvidence) {
      activityList.innerHTML = "";
      activityEmpty.classList.remove("hidden");
    } else {
      const sourceHref = safeHttpUrl(matchingActivityEvidence.source_url);
      const source = sourceHref
        ? `<a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(matchingActivityEvidence.source_title || matchingActivityEvidence.source_url)}</a>`
        : escapeHtml(matchingActivityEvidence.source_title || "Source not reported");
      activityList.innerHTML = `
        <li class="activity-entry">
          <span class="activity-marker" aria-hidden="true"></span>
          <div class="activity-content">
            <p>${escapeHtml(matchingActivityEvidence.value || activity)}</p>
            <div class="activity-meta">
              <time datetime="${escapeHtml(matchingActivityEvidence.publication_date)}">Published ${escapeHtml(formatDate(matchingActivityEvidence.publication_date, true))}</time>
              <span aria-hidden="true">·</span>
              <span>${source}</span>
            </div>
          </div>
        </li>
      `;
      activityEmpty.classList.add("hidden");
    }

    const tbody = document.getElementById("evidence-table-body");
    tbody.innerHTML = "";
    document.getElementById("evidence-stats").textContent = `${evidenceList.length} citations tracked`;

    evidenceList.forEach((ev) => {
      const tr = document.createElement("tr");
      const isOfficial = ev.source_type === "official_registry";
      const badgeClass = isOfficial ? "src-official" : "src-web";
      const label = isOfficial ? "Official Registry" : "Web / Search";
      const sourceHref = safeHttpUrl(ev.source_url);
      const dates = [
        ev.retrieved_at ? `Retrieved ${formatDate(ev.retrieved_at)}` : null,
        ev.publication_date ? `Published ${formatDate(ev.publication_date)}` : null
      ].filter(Boolean).join("\n") || "Not reported";
      tr.innerHTML = `
        <td><strong>${escapeHtml(ev.field)}</strong></td>
        <td><code>${escapeHtml(formatValue(ev.value))}</code></td>
        <td><span class="src-badge ${badgeClass}">${label}</span></td>
        <td>
          ${sourceHref ? `<a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer" class="src-link">${escapeHtml(ev.source_title || ev.source_url)} ↗</a><span class="source-url">${escapeHtml(ev.source_url)}</span>` : escapeHtml(ev.source_title || "Source not reported")}
          <div class="src-quote">${escapeHtml(ev.explanation || "No supporting quote available.")}</div>
        </td>
        <td><strong>${Math.round((ev.confidence || 0) * 100)}%</strong></td>
        <td class="evidence-dates">${escapeHtml(dates)}</td>
      `;
      tbody.appendChild(tr);
    });

    resultsContainer.classList.remove("hidden");
  }

  function safeHttpUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, window.location.origin);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch {
      return null;
    }
  }

  function formatLabel(value) {
    return String(value || "Information").replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function formatDate(value, dateOnly = false) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString([], dateOnly
      ? { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }
      : undefined);
  }

  function renderHistoryModal(data) {
    historyRuns.innerHTML = `
      <div style="margin-bottom: 12px; font-size: 13px; color: var(--text-muted);">
        Total Research Executions: <strong>${data.runs.length}</strong>
      </div>
    `;

    historyChanges.innerHTML = "";
    if (data.history.length === 0) {
      historyChanges.innerHTML = "<p style='color: var(--text-muted);'>No field modifications logged.</p>";
      return;
    }

    data.history.forEach((h) => {
      const item = document.createElement("div");
      item.className = "history-item";
      let badgeClass = "diff-unchanged";
      if (h.change_type === "added") badgeClass = "diff-added";
      if (h.change_type === "modified") badgeClass = "diff-modified";

      item.innerHTML = `
        <div>
          <span class="diff-badge ${badgeClass}">${h.change_type.toUpperCase()}</span>
          <strong>${escapeHtml(h.field_name)}</strong>
          <span style="float: right; color: var(--text-dim); font-size: 11px;">${new Date(h.changed_at).toLocaleString()}</span>
        </div>
        <div style="margin-top: 4px; font-size: 12px;">
          ${h.old_value !== null ? `<span style="color: var(--danger); text-decoration: line-through;">Old: ${escapeHtml(JSON.stringify(h.old_value))}</span> &rarr; ` : ''}
          <span style="color: var(--success);">Value: ${escapeHtml(JSON.stringify(h.new_value))}</span>
        </div>
      `;
      historyChanges.appendChild(item);
    });
  }

  function formatValue(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
