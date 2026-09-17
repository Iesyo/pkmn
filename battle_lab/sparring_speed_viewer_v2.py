#!/usr/bin/env python3
"""Robust Speed Tier viewer for Battle Lab Sparring.

The first Speed Tier prototype piggybacked on the native-control wrapper's poll
and hid itself whenever the inferred free gutter was too small or speed data was
not ready. This viewer keeps Showdown vendor files untouched, but owns an
independent Speed Tier poll and a deterministic left column. If the backend
stops exposing speedTier, the panel stays visible with a diagnostic instead of
silently disappearing.
"""

from __future__ import annotations

import argparse
import http.server
from functools import partial
from pathlib import Path

from battle_lab import sparring_speed_viewer as legacy


BRIDGE_MARKER = "battle-lab-native-showdown-controls-v4-speed-tier"

EXTRA_CSS = r'''
#speed-tier{
  display:block!important;
  left:16px!important;
  top:16px!important;
  width:296px!important;
  max-height:calc(100% - 32px)!important;
  z-index:20!important;
}
#speed-tier:empty::before{
  content:'⚡ Speed Tier · inicializando…';
  display:block;
  padding:14px;
  color:#94a3b8;
  font:800 11px/1.4 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
@media (min-width:1180px){
  #showdown{
    left:328px!important;
    right:auto!important;
    width:calc(100% - 328px)!important;
  }
}
@media (max-width:1179px){
  #speed-tier{
    width:270px!important;
    max-height:55%!important;
    background:rgba(2,8,23,.97)!important;
  }
}
.st-diagnostic{padding:13px;color:#cbd5e1;font-size:10px;line-height:1.45}
.st-diagnostic strong{display:block;color:#f8fafc;margin-bottom:4px}
'''

EXTRA_JS = r'''
<script>
(function () {
  'use strict';
  var marker = 'battle-lab-native-showdown-controls-v4-speed-tier';
  var panel = document.getElementById('speed-tier');
  if (!panel) return;
  var api = location.protocol + '//' + location.hostname + ':8765';
  var lastKey = '';

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function species(value) {
    var text = String(value || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[-_]+/g, ' ');
    return text ? text.replace(/\b\w/g, function (letter) { return letter.toUpperCase(); }) : 'Pokémon';
  }
  function badge(text, klass) {
    return '<span class="st-badge ' + (klass || '') + '">' + esc(text) + '</span>';
  }
  function diagnostic(title, detail) {
    panel.dataset.hasData = '1';
    panel.innerHTML = '<div class="st-head"><div class="st-kicker">⚡ Speed Tier</div><div class="st-title">ORDEN DE TURNO</div></div>' +
      '<div class="st-diagnostic"><strong>' + esc(title) + '</strong>' + esc(detail || '') + '</div>';
  }
  function render(tier) {
    if (!tier || typeof tier !== 'object') {
      diagnostic('Esperando datos de Speed Tier…', 'El panel ya está montado; falta el snapshot del runtime.');
      return;
    }
    var order = Array.isArray(tier.order) ? tier.order : [];
    var priority = Array.isArray(tier.priority) ? tier.priority : [];
    var notes = Array.isArray(tier.notes) ? tier.notes : [];
    if (!order.length && !priority.length) {
      diagnostic('Speed Tier sin filas', notes.join(' · ') || 'El backend no publicó Pokémon activos todavía.');
      return;
    }

    var badges = '';
    if (tier.trickRoom) badges += badge('Trick Room' + (tier.trickRoomTurns ? ' · ' + tier.trickRoomTurns + 'T' : ''), 'tr');
    if (tier.ownTailwind) badges += badge('Tailwind · Tú' + (tier.ownTailwindTurns ? ' · ' + tier.ownTailwindTurns + 'T' : ''), 'own');
    if (tier.opponentTailwind) badges += badge('Tailwind · Rival' + (tier.opponentTailwindTurns ? ' · ' + tier.opponentTailwindTurns + 'T' : ''), 'opp');

    var rows = order.map(function (row, index) {
      var side = row && row.side === 'opponent' ? 'opponent' : 'own';
      var label = side === 'own' ? 'Tú' : 'Rival';
      var modifiers = row && Array.isArray(row.modifiers) ? row.modifiers.slice() : [];
      if (row && row.rawSpeed != null && row.effectiveSpeed != null && Number(row.rawSpeed) !== Number(row.effectiveSpeed)) modifiers.unshift('Base ' + row.rawSpeed);
      var uncertainty = row && Array.isArray(row.uncertainty) && row.uncertainty.length ? ' · ⚠' : '';
      return '<div class="st-row ' + side + '"><span class="st-rank">' + (index + 1) + '</span><div><div class="st-name">' + esc(species(row && (row.species || row.name))) + '<span class="st-side ' + side + '">' + label + '</span></div><div class="st-meta">' + esc((modifiers.join(' · ') || 'Sin modificadores') + uncertainty) + '</div></div><div class="st-speed">' + esc(row && row.effectiveSpeed != null ? row.effectiveSpeed : '?') + '<small>Speed</small></div></div>';
    }).join('');

    var priorities = priority.slice(0, 12).map(function (entry) {
      var numeric = Number(entry && entry.priority) || 0;
      var mods = entry && Array.isArray(entry.modifiers) && entry.modifiers.length ? ' · ' + entry.modifiers.join(' · ') : '';
      return '<div class="st-priority"><div class="st-pnum ' + (numeric < 0 ? 'neg' : '') + '">' + (numeric > 0 ? '+' : '') + numeric + '</div><div><div class="st-pname">' + esc(species(entry && entry.species)) + ' · ' + esc(entry && entry.label) + '</div><div class="st-pmeta">' + esc((entry && entry.side === 'opponent' ? 'Rival' : 'Tú') + mods) + '</div></div></div>';
    }).join('');

    var warnings = [];
    order.forEach(function (row) {
      if (!row || !Array.isArray(row.uncertainty)) return;
      row.uncertainty.forEach(function (message) {
        var full = species(row.species || row.name) + ': ' + message;
        if (warnings.indexOf(full) < 0) warnings.push(full);
      });
    });

    panel.dataset.hasData = '1';
    panel.innerHTML = '<div class="st-head"><div class="st-kicker">⚡ Speed Tier</div><div class="st-title">ORDEN DE TURNO</div><div class="st-sub">Prioridad → Speed ' + (tier.trickRoom ? '(baja → alta por Trick Room)' : '(alta → baja)') + '.</div>' + (badges ? '<div class="st-badges">' + badges + '</div>' : '') + '</div><div class="st-section"><div class="st-section-title"><span>Speed · mismo bracket</span><span>T' + esc(tier.turn == null ? '?' : tier.turn) + '</span></div>' + rows + '</div>' + (priorities ? '<div class="st-section"><div class="st-section-title"><span>Prioridad</span><span>amenazas</span></div>' + priorities + '</div>' : '') + (warnings.length ? '<div class="st-warning">⚠ ' + esc(warnings.slice(0, 3).join(' · ')) + '</div>' : '');
  }

  async function poll() {
    try {
      var healthResponse = await fetch(api + '/health', {cache: 'no-store'});
      if (!healthResponse.ok) {
        diagnostic('Battle Lab no responde', 'HTTP ' + healthResponse.status + ' en :8765.');
        return;
      }
      var health = await healthResponse.json();
      var sessionId = health && health.activeSession ? String(health.activeSession) : '';
      if (!sessionId) {
        diagnostic('Esperando Sparring…', 'Inicia una batalla para calcular el orden.');
        return;
      }
      var response = await fetch(api + '/sparring/' + encodeURIComponent(sessionId), {cache: 'no-store'});
      if (!response.ok) {
        diagnostic('No pude leer la sesión', 'HTTP ' + response.status + '.');
        return;
      }
      var snapshot = await response.json();
      var tier = snapshot && snapshot.battle ? snapshot.battle.speedTier : null;
      var key = JSON.stringify(tier || null);
      if (key !== lastKey || panel.getAttribute('data-speed-tier-v4') !== marker) {
        lastKey = key;
        panel.setAttribute('data-speed-tier-v4', marker);
        render(tier);
      }
    } catch (error) {
      diagnostic('Error leyendo Speed Tier', error && error.message ? error.message : String(error));
    }
  }

  setInterval(function () { void poll(); }, 350);
  void poll();
})();
</script>
'''


def build_bridge_html() -> str:
    html = legacy.BRIDGE_HTML
    if 'data-speed-tier-layout="v2"' in html:
        return html
    html = html.replace('</style>', EXTRA_CSS.strip() + '\n</style>', 1)
    html = html.replace('<body>', '<body data-speed-tier-layout="v2">', 1)
    html = html.replace('</body>', EXTRA_JS + '\n</body>', 1)
    required = ['data-speed-tier-layout="v2"', 'battle-lab-native-showdown-controls-v4-speed-tier', 'Speed Tier · inicializando']
    missing = [token for token in required if token not in html]
    if missing:
        raise RuntimeError('No se pudo montar Speed Tier v2: ' + ', '.join(missing))
    return html


BRIDGE_HTML = build_bridge_html()


class BattleLabSpeedViewerV2Handler(legacy.BattleLabSpeedViewerHandler):
    server_version = "BattleLabShowdownSpeedViewer/2"

    def _serve_bridge(self, *, head_only: bool = False) -> None:
        payload = BRIDGE_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Battle-Lab-Bridge', BRIDGE_MARKER)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def _serve_health(self, *, head_only: bool = False) -> None:
        payload = (BRIDGE_MARKER + '\n').encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f'No existe el checkout del cliente Showdown: {root}')
    handler = partial(BattleLabSpeedViewerV2Handler, directory=str(root))
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
