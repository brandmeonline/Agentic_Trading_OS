/*
 * Operator status bar refresh - ATOS-P2-UI-001 / INV-ATOS-012.
 *
 * The bar is server-rendered, so it is already correct when the page loads.
 * This only keeps it current. The important behaviour is the failure path:
 * if the refresh cannot reach the server, the bar degrades to UNKNOWN rather
 * than keeping the last good values on screen. A stale green badge is the
 * precise thing this issue exists to prevent.
 */
(function () {
    "use strict";

    var REFRESH_MS = 10000;
    var bar = document.getElementById("operator-bar");
    if (!bar) { return; }

    function setText(id, text) {
        var el = document.getElementById(id);
        if (el) { el.textContent = text; }
    }

    function flag(id, on) {
        var el = document.getElementById(id);
        if (el) { el.classList.toggle("breach", !!on); }
    }

    function ago(seconds) {
        if (seconds === null || seconds === undefined) { return "never"; }
        return Math.round(seconds) + "s ago";
    }

    function money(value) {
        if (value === null || value === undefined) { return "no tier"; }
        return "$" + Math.round(value).toLocaleString();
    }

    function render(status) {
        bar.className = "operator-bar level-" + status.banner.level;
        setText("op-mode", "MODE " + status.mode);
        setText("op-broker", "BROKER " + status.broker + " · " + status.broker_account);
        setText("op-data", "DATA " + status.data);
        setText("op-exec", "EXEC " + status.execution);
        setText("op-recon", "RECON " + status.reconciliation);

        var dataBadge = document.getElementById("op-data");
        if (dataBadge) {
            dataBadge.className = "op-badge op-data trust-" + String(status.data).toLowerCase();
        }

        setText("op-broker-age", "broker " + ago(status.broker_connection_age_seconds));
        setText("op-data-age", "data " + ago(status.market_data_age_seconds));
        setText("op-capital", "risk " + money(status.effective_capital_at_risk) +
                " / " + money(status.capital_tier_limit));
        setText("op-reserved", "reserved " + money(status.reserved_capital));
        setText("op-orders", "orders " + status.open_order_count + " open / " +
                status.unknown_order_count + " unknown");
        setText("op-trips", "trips " + (status.active_risk_trips.length
                ? status.active_risk_trips.join(", ") : "none"));
        setText("op-persist", "persistence " +
                (status.persistence_healthy ? "ok" : "unconfirmed"));

        flag("op-capital", status.capital_breached);
        flag("op-orders", status.unknown_order_count > 0);
        flag("op-trips", status.active_risk_trips.length > 0);
        flag("op-persist", !status.persistence_healthy);

        var banner = document.getElementById("operator-banner");
        if (banner) {
            banner.textContent = status.banner.text;
            banner.title = (status.problems || []).join("\n");
        }
    }

    function degrade(reason) {
        bar.className = "operator-bar level-critical";
        setText("op-mode", "MODE UNKNOWN");
        setText("op-broker", "BROKER UNKNOWN");
        setText("op-data", "DATA UNAVAILABLE");
        setText("op-exec", "EXEC UNKNOWN");
        setText("op-recon", "RECON UNKNOWN");
        var dataBadge = document.getElementById("op-data");
        if (dataBadge) { dataBadge.className = "op-badge op-data trust-unavailable"; }
        setText("operator-banner",
                "status unavailable (" + reason + "); the values on this page are not confirmed");
    }

    function poll() {
        fetch("/api/operator-status", { credentials: "same-origin" })
            .then(function (response) {
                if (!response.ok) { throw new Error("HTTP " + response.status); }
                return response.json();
            })
            .then(render)
            .catch(function (error) { degrade(error.message || "unreachable"); });
    }

    poll();
    window.setInterval(poll, REFRESH_MS);
}());
