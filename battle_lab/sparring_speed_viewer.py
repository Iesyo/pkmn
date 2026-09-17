#!/usr/bin/env python3
"""Battle Lab classic Showdown viewer with an advisory Speed Tier overlay.

Vendor files stay untouched.  This module decorates the existing Battle Lab
wrapper at runtime: it keeps native Showdown controls/animations and uses the
otherwise empty left gutter for turn-order information.
"""

from __future__ import annotations

import argparse
import http.server
from functools import partial
from pathlib import Path

from battle_lab import showdown_native_viewer as base

BRIDGE_MARKER = "battle-lab-native-showdown-controls-v3-speed-tier"

PANEL_CSS = r'''
html,body{width:100%;height:100%;margin:0;border:0;overflow:hidden;background:#444}
body{position:relative}
#showdown{position:absolute;inset:0;z-index:1;display:block;width:100%;height:100%;margin:0;border:0;background:#444}
#speed-tier{position:absolute;z-index:7;left:18px;top:70px;display:none;width:300px;max-height:calc(100% - 92px);overflow:auto;box-sizing:border-box;border:1px solid rgba(103,232,249,.18);border-radius:16px;background:rgba(2,8,23,.94);box-shadow:0 18px 45px rgba(0,0,0,.28);color:#dbeafe;font:12px/1.35 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
#speed-tier::-webkit-scrollbar{width:6px}#speed-tier::-webkit-scrollbar-thumb{background:rgba(148,163,184,.25);border-radius:999px}
.st-head{padding:12px 13px 10px;border-bottom:1px solid rgba(148,163,184,.12)}.st-kicker{font-size:9px;font-weight:900;letter-spacing:.14em;color:#67e8f9;text-transform:uppercase}.st-title{margin-top:2px;font-size:15px;font-weight:900;color:#f8fafc}.st-sub{margin-top:3px;font-size:9px;color:#64748b}.st-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.st-badge{border:1px solid rgba(148,163,184,.16);border-radius:999px;padding:2px 7px;font-size:9px;color:#cbd5e1;background:rgba(15,23,42,.72)}.st-badge.tr{border-color:rgba(196,181,253,.28);color:#ddd6fe}.st-badge.own{border-color:rgba(103,232,249,.24);color:#a5f3fc}.st-badge.opp{border-color:rgba(251,113,133,.24);color:#fecdd3}
.st-section{padding:10px 10px 0}.st-section:last-child{padding-bottom:11px}.st-section-title{display:flex;align-items:center;justify-content:space-between;padding:0 3px 6px;font-size:8px;font-weight:900;letter-spacing:.14em;color:#64748b;text-transform:uppercase}.st-row{display:grid;grid-template-columns:22px 1fr auto;gap:7px;align-items:center;margin-bottom:6px;padding:7px 8px;border:1px solid rgba(148,163,184,.10);border-radius:11px;background:rgba(15,23,42,.62)}.st-row.own{border-color:rgba(103,232,249,.15)}.st-row.opponent{border-color:rgba(251,113,133,.14)}.st-rank{display:flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:7px;background:rgba(30,41,59,.9);font-size:9px;font-weight:900;color:#94a3b8}.st-name{font-size:10px;font-weight:900;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.st-side{margin-left:4px;font-size:7px;font-weight:800;text-transform:uppercase}.st-side.own{color:#67e8f9}.st-side.opponent{color:#fb7185}.st-meta{margin-top:2px;font-size:8px;color:#64748b;white-space:normal}.st-speed{text-align:right;font-size:13px;font-weight:950;color:#f8fafc}.st-speed small{display:block;font-size:7px;font-weight:700;color:#64748b}
.st-priority{display:grid;grid-template-columns:31px 1fr;gap:7px;align-items:center;margin-bottom:5px;padding:6px 8px;border-radius:10px;background:rgba(15,23,42,.48)}.st-pnum{font-size:11px;font-weight:950;text-align:center;color:#fbbf24}.st-pnum.neg{color:#a78bfa}.st-pname{font-size:9px;font-weight:850;color:#e2e8f0}.st-pmeta{font-size:8px;color:#64748b}.st-warning{margin:0 10px 10px;padding:8px 9px;border:1px solid rgba(251,191,36,.14);border-radius:10px;background:rgba(251,191,36,.05);font-size:8px;color:#fcd34d}.st-empty{padding:14px;text-align:center;font-size:9px;color:#64748b}
'''

PANEL_JS = r'''
  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function labelSpecies(value) {
    var text = String(value || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[-_]+/g, ' ');
    return text ? text.replace(/\b\w/g, function (letter) { return letter.toUpperCase(); }) : 'Pokémon';
  }
  function turnBadge(label, active, turns, cssClass) {
    if (!active) return '';
    var suffix = Number.isFinite(Number(turns)) && Number(turns) > 0 ? ' · ' + Number(turns) + 'T' : '';
    return '<span class="st-badge ' + cssClass + '">' + escapeHtml(label + suffix) + '</span>';
  }
  function fitSpeedTier() {
    if (!speedPanel || speedPanel.dataset.hasData !== '1') return;
    var available = 0;
    try {
      var child = frame.contentWindow;
      var battle = child && child.document ? child.document.querySelector('.battle') : null;
      if (battle) {
        var rect = battle.getBoundingClientRect();
        if (rect && rect.left > 0) available = Math.floor(rect.left - 32);
      }
    } catch (_) {}
    if (!available) available = Math.floor((frame.clientWidth - 1000) / 2 - 28);
    if (available < 230) { speedPanel.style.display = 'none'; return; }
    speedPanel.style.width = Math.max(230, Math.min(315, available)) + 'px';
    speedPanel.style.display = 'block';
  }
  function renderSpeedTier(tier) {
    if (!speedPanel) return;
    var order = tier && Array.isArray(tier.order) ? tier.order : [];
    var priority = tier && Array.isArray(tier.priority) ? tier.priority : [];
    if (!tier || (!order.length && !priority.length)) {
      speedPanel.dataset.hasData = '0'; speedPanel.style.display = 'none'; speedPanel.innerHTML = ''; return;
    }
    var badges = '';
    badges += turnBadge('Trick Room', !!tier.trickRoom, tier.trickRoomTurns, 'tr');
    badges += turnBadge('Tailwind · Tú', !!tier.ownTailwind, tier.ownTailwindTurns, 'own');
    badges += turnBadge('Tailwind · Rival', !!tier.opponentTailwind, tier.opponentTailwindTurns, 'opp');
    var rows = order.map(function (row, index) {
      var side = row.side === 'opponent' ? 'opponent' : 'own';
      var sideLabel = side === 'own' ? 'Tú' : 'Rival';
      var meta = Array.isArray(row.modifiers) ? row.modifiers.slice() : [];
      if (row.rawSpeed != null && row.effectiveSpeed != null && Number(row.rawSpeed) !== Number(row.effectiveSpeed)) meta.unshift('Base ' + row.rawSpeed);
      var warning = Array.isArray(row.uncertainty) && row.uncertainty.length ? ' · ⚠' : '';
      return '<div class="st-row ' + side + '"><span class="st-rank">' + (index + 1) + '</span><div><div class="st-name">' + escapeHtml(labelSpecies(row.species || row.name)) + '<span class="st-side ' + side + '">' + sideLabel + '</span></div><div class="st-meta">' + escapeHtml((meta.join(' · ') || 'Sin modificadores') + warning) + '</div></div><div class="st-speed">' + escapeHtml(row.effectiveSpeed == null ? '?' : row.effectiveSpeed) + '<small>Speed</small></div></div>';
    }).join('');
    var priorityRows = priority.map(function (entry) {
      var side = entry.side === 'opponent' ? 'Rival' : 'Tú';
      var numeric = Number(entry.priority) || 0;
      var modifiers = Array.isArray(entry.modifiers) && entry.modifiers.length ? ' · ' + entry.modifiers.join(' · ') : '';
      return '<div class="st-priority"><div class="st-pnum ' + (numeric < 0 ? 'neg' : '') + '">' + (numeric > 0 ? '+' : '') + numeric + '</div><div><div class="st-pname">' + escapeHtml(labelSpecies(entry.species)) + ' · ' + escapeHtml(entry.label) + '</div><div class="st-pmeta">' + escapeHtml(side + modifiers) + '</div></div></div>';
    }).join('');
    var warnings = [];
    order.forEach(function (row) {
      if (Array.isArray(row.uncertainty)) row.uncertainty.forEach(function (message) {
        var full = labelSpecies(row.species || row.name) + ': ' + message;
        if (warnings.indexOf(full) < 0) warnings.push(full);
      });
    });
    speedPanel.innerHTML = '<div class="st-head"><div class="st-kicker">⚡ Speed Tier</div><div class="st-title">ORDEN DE TURNO</div><div class="st-sub">Prioridad → Speed ' + (tier.trickRoom ? '(baja → alta por Trick Room)' : '(alta → baja)') + '.</div>' + (badges ? '<div class="st-badges">' + badges + '</div>' : '') + '</div><div class="st-section"><div class="st-section-title"><span>Speed · mismo bracket</span><span>T' + escapeHtml(tier.turn == null ? '?' : tier.turn) + '</span></div>' + (rows || '<div class="st-empty">Esperando Pokémon activos…</div>') + '</div>' + (priorityRows ? '<div class="st-section"><div class="st-section-title"><span>Prioridad</span><span>amenazas</span></div>' + priorityRows + '</div>' : '') + (warnings.length ? '<div class="st-warning">⚠ ' + escapeHtml(warnings.slice(0, 3).join(' · ')) + '</div>' : '');
    speedPanel.dataset.hasData = '1'; fitSpeedTier();
  }
  function pinSinglePanel() {
    try {
      var child = frame.contentWindow;
      if (!child || !child.app) return false;
      var changed = false;
      if (!child.app.singlePanelMode) { child.app.singlePanelMode = true; changed = true; }
      if (child.app.curSideRoom) { if (typeof child.app.curSideRoom.hide === 'function') child.app.curSideRoom.hide(); child.app.curSideRoom = null; changed = true; }
      if (changed && typeof child.app.updateLayout === 'function') child.app.updateLayout();
      return true;
    } catch (_) { return false; }
  }
'''


def decorate_bridge_html(html: str) -> str:
    """Decorate either the stock Battle Lab wrapper or the cropped shell variant."""

    if 'id="speed-tier"' in html:
        return html
    if "</style>" not in html or '<iframe id="showdown"' not in html:
        raise RuntimeError("El wrapper de Showdown cambió y no expone los anclajes del Speed Tier.")

    # Appended rules intentionally override either wrapper layout without touching vendor.
    html = html.replace("</style>", PANEL_CSS.strip() + "\n  </style>", 1)
    html = html.replace(
        '<iframe id="showdown" title="Pokémon Showdown"></iframe>',
        '<iframe id="showdown" title="Pokémon Showdown"></iframe>\n<aside id="speed-tier" aria-label="Battle Lab Speed Tier"></aside>',
        1,
    )
    if "var speedPanel = document.getElementById('speed-tier');" not in html:
        html = html.replace(
            "var frame = document.getElementById('showdown');",
            "var frame = document.getElementById('showdown');\n  var speedPanel = document.getElementById('speed-tier');",
            1,
        )
    if "function renderSpeedTier(" not in html:
        html = html.replace("\n  function localizeBattleFxUrl(value) {", "\n" + PANEL_JS + "\n  function localizeBattleFxUrl(value) {", 1)

    if "function childRoom() {\n    pinLocalBattleAssets();\n    pinSinglePanel();" not in html:
        html = html.replace(
            "function childRoom() {\n    pinLocalBattleAssets();",
            "function childRoom() {\n    pinLocalBattleAssets();\n    pinSinglePanel();",
            1,
        )
    if "async function poll() {\n    try {\n      pinLocalBattleAssets();\n      pinSinglePanel();" not in html:
        html = html.replace(
            "async function poll() {\n    try {\n      pinLocalBattleAssets();",
            "async function poll() {\n    try {\n      pinLocalBattleAssets();\n      pinSinglePanel();",
            1,
        )
    if "if (!activeSession) { renderSpeedTier(null); return; }" not in html:
        html = html.replace("if (!activeSession) return;", "if (!activeSession) { renderSpeedTier(null); return; }", 1)
    if "renderSpeedTier(snapshot && snapshot.battle ? snapshot.battle.speedTier : null);" not in html:
        html = html.replace(
            "lastSnapshot = snapshot;\n      applyRequest(snapshot, false);",
            "lastSnapshot = snapshot;\n      renderSpeedTier(snapshot && snapshot.battle ? snapshot.battle.speedTier : null);\n      applyRequest(snapshot, false);\n      setTimeout(fitSpeedTier, 0);",
            1,
        )
    if "frame.addEventListener('load', function () {\n    pinLocalBattleAssets();\n    pinSinglePanel();" not in html:
        html = html.replace(
            "frame.addEventListener('load', function () {\n    pinLocalBattleAssets();",
            "frame.addEventListener('load', function () {\n    pinLocalBattleAssets();\n    pinSinglePanel();",
            1,
        )
    if "window.addEventListener('resize', fitSpeedTier);" not in html:
        html = html.replace(
            "setInterval(function () { void poll(); }, 300);",
            "window.addEventListener('resize', fitSpeedTier);\n  setInterval(function () { void poll(); }, 300);",
            1,
        )

    required = ["id=\"speed-tier\"", "function renderSpeedTier", "renderSpeedTier(snapshot", "pinSinglePanel"]
    missing = [token for token in required if token not in html]
    if missing:
        raise RuntimeError("No se pudo decorar el wrapper de Showdown: " + ", ".join(missing))
    return html


def build_bridge_html() -> str:
    return decorate_bridge_html(base.BRIDGE_HTML)


BRIDGE_HTML = build_bridge_html()


class BattleLabSpeedViewerHandler(base.BattleLabViewerHandler):
    server_version = "BattleLabShowdownSpeedViewer/1"

    def _serve_bridge(self, *, head_only: bool = False) -> None:
        payload = BRIDGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Battle-Lab-Bridge", BRIDGE_MARKER)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def _serve_health(self, *, head_only: bool = False) -> None:
        payload = (BRIDGE_MARKER + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"No existe el checkout del cliente Showdown: {root}")
    handler = partial(BattleLabSpeedViewerHandler, directory=str(root))
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
