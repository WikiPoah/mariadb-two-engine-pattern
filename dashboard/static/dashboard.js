"use strict";

const REFRESH_MS = 5000;
const REQUEST_TIMEOUT_MS = 15000;

// Preserve decimal money strings rather than rounding them through binary floats.
function euros(value) {
  const [whole, fraction = ""] = String(value).split(".");
  return `€${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction.padEnd(2, "0")}`;
}

function utc(value) {
  return new Date(value).toISOString().replace("T", " ").replace("Z", " UTC");
}

function tableRows(panel, rows, columns, emptyMessage) {
  const body = document.createElement("tbody");
  if (rows.length === 0) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = columns;
    cell.className = "empty";
    cell.textContent = emptyMessage;
  } else {
    for (const values of rows) {
      const row = body.insertRow();
      for (const value of values) row.insertCell().textContent = value;
    }
  }
  panel.querySelector("tbody").replaceWith(body);
}

const renderers = {
  overview(panel, data) {
    for (const element of panel.querySelectorAll("[data-metric]")) {
      const key = element.dataset.metric;
      element.textContent = key === "revenue" ? euros(data[key]) : data[key].toLocaleString("en-IE");
    }
  },
  inventory(panel, data) {
    tableRows(panel, data.map(row => [row.product_name, euros(row.current_price), row.stock,
      row.is_active ? "Active" : "Inactive"]), 4, "No products available.");
  },
  activity(panel, data) {
    tableRows(panel, data.map(row => [utc(row.occurred_at),
      row.event_type === "session_start" ? "Session start" : "Product view",
      `${row.session_id.slice(0, 8)}…${row.session_id.slice(-4)}`,
      row.campaign_id === null ? "Organic" : row.campaign_id, row.product_id ?? "—"
    ]), 5, "No activity recorded.");
  },
  campaigns(panel, data) {
    tableRows(panel, data.map(row => [row.is_organic ? "Organic" : row.campaign_name,
      row.sessions, row.purchasing_sessions,
      `${String(row.conversion_percent).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")}%`, euros(row.revenue),
      row.is_organic ? "—" : row.is_active ? "Active" : "Paused"
    ]), 6, "No session acquisition recorded.");
  }
};

async function refreshPanel(name, render) {
  const panel = document.getElementById(name);
  const status = panel.querySelector(".panel-status");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(panel.dataset.url, {signal: controller.signal, cache: "no-store"});
    if (!response.ok) throw new Error("Panel read failed");
    const result = await response.json();
    const updated = utc(result.observed_at);
    render(panel, result.data);
    panel.dataset.updated = updated;
    status.textContent = `Last updated: ${updated}`;
    status.classList.remove("error");
  } catch {
    status.textContent = panel.dataset.updated
      ? `Unable to refresh. Showing previous data. Last updated: ${panel.dataset.updated}`
      : "Unable to load this panel. Will retry on the next refresh.";
    status.classList.add("error");
  } finally {
    clearTimeout(timeout);
  }
}

async function refresh() {
  await Promise.all(Object.entries(renderers).map(([name, render]) => refreshPanel(name, render)));
  // Schedule after completion so slow requests never overlap or trigger catch-up bursts.
  setTimeout(refresh, REFRESH_MS);
}

refresh();
