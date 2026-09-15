#!/usr/bin/env python3
"""Static Showdown viewer with a transparent native-controls bridge.

The pinned Pokémon Showdown checkout is never modified. Browser requests for
``testclient-old.html`` receive a tiny Battle Lab wrapper that embeds the real
vendor page from an alias. The wrapper polls the Sparring API on the same host
that served the viewer, injects the raw player request into the native
BattleRoom, and sends native `/team` or `/choose` decisions back to Battle Lab.

When the viewer is opened through loopback, the API remains loopback. When an
explicit LAN runtime exposes the viewer on a private address, the same bridge
uses that address automatically instead of accidentally targeting the remote
browser's own 127.0.0.1.

Nana's compatibility verifier uses a dedicated User-Agent; for that request the
original vendor HTML is returned byte-for-byte so an occupied compatible viewer
can still be verified safely.
"""

from __future__ import annotations

import argparse
import http.server
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit


BRIDGE_MARKER = "battle-lab-native-showdown-controls-v1"
CLASSIC_PATH = "/play.pokemonshowdown.com/testclient-old.html"
VENDOR_ALIAS = "/play.pokemonshowdown.com/battle-lab-vendor.html"
HEALTH_PATH = "/battle-lab-native-controls-health"
VERIFY_USER_AGENT = "like-no-one-ever-was-nana/0"


BRIDGE_HTML = r'''<!doctype html>
<html lang="en" data-battle-lab-native-controls="v1">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Battle Lab · Pokémon Showdown</title>
  <style>
    html,body,#showdown{width:100%;height:100%;margin:0;border:0;overflow:hidden;background:#444}
    #showdown{display:block}
  </style>
</head>
<body>
<iframe id="showdown" title="Pokémon Showdown"></iframe>
<script>
(function () {
  'use strict';
  var marker = 'battle-lab-native-showdown-controls-v1';
  var api = location.protocol + '//' + location.hostname + ':8765';
  var frame = document.getElementById('showdown');
  var roomid = (location.hash || '').replace(/^#/, '');
  var activeSession = '';
  var lastSnapshot = null;
  var lastAppliedKey = '';
  var submitting = false;

  frame.src = 'battle-lab-vendor.html' + location.search + location.hash;

  function childRoom() {
    var child = frame.contentWindow;
    if (!child || !child.app || !roomid) return null;
    var rooms = child.app.rooms || {};
    var room = rooms[roomid];
    if (!room || room.id !== roomid || typeof room.receiveRequest !== 'function') return null;
    return room;
  }

  function showError(message) {
    try {
      var child = frame.contentWindow;
      if (child && child.app && typeof child.app.addPopupMessage === 'function') {
        child.app.addPopupMessage(String(message || 'Battle Lab rechazó la jugada.'));
      }
    } catch (_) {}
  }

  function requestKey(snapshot) {
    if (!snapshot || !snapshot.nativeRequest) return '';
    return [
      snapshot.id || '',
      snapshot.phase || '',
      snapshot.generation || 0,
      JSON.stringify(snapshot.nativeRequest)
    ].join('|');
  }

  function applyRequest(snapshot, force) {
    if (!snapshot || !snapshot.nativeRequest) return false;
    if (snapshot.phase !== 'native-team-preview' && snapshot.phase !== 'native-waiting-choice') return false;
    var room = childRoom();
    if (!room) return false;
    patchRoom(room);
    var key = requestKey(snapshot);
    if (!force && key === lastAppliedKey) return true;
    var request = JSON.parse(JSON.stringify(snapshot.nativeRequest));

    // The modern Showdown request tells us how many Pokémon must be chosen
    // through maxChosenTeamSize. The pinned classic client predates that field
    // and otherwise falls back to the doubles lead count (2), which is wrong
    // for VGC bring-6-pick-4. Seed the Battle object's native preview count
    // before receiveRequest() so the unmodified classic BattleRoom asks for
    // exactly the server-required number of Pokémon.
    if (request.teamPreview && room.battle) {
      var chosenTeamSize = parseInt(request.maxChosenTeamSize || 0, 10);
      if (chosenTeamSize > 0 && request.side && Array.isArray(request.side.pokemon) && chosenTeamSize <= request.side.pokemon.length) {
        room.battle.teamPreviewCount = chosenTeamSize;
      }
    }

    room.receiveRequest(request, null);
    lastAppliedKey = key;
    return true;
  }

  async function submitChoice(command) {
    if (!activeSession || submitting) return;
    submitting = true;
    try {
      var response = await fetch(api + '/sparring/' + encodeURIComponent(activeSession) + '/native-choice', {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({command: command})
      });
      if (!response.ok) {
        var payload = {};
        try { payload = await response.json(); } catch (_) {}
        var detail = payload && payload.detail ? payload.detail : 'Battle Lab rechazó la jugada nativa.';
        lastAppliedKey = '';
        applyRequest(lastSnapshot, true);
        showError(detail);
      }
    } catch (error) {
      lastAppliedKey = '';
      applyRequest(lastSnapshot, true);
      showError(error && error.message ? error.message : error);
    } finally {
      submitting = false;
    }
  }

  function patchRoom(room) {
    if (room.__battleLabNativeControlsBridge === marker) return;
    var originalSend = room.send;
    room.send = function (message) {
      if (typeof message === 'string' && (/^\/(?:choose |team )/.test(message))) {
        void submitChoice(message);
        return;
      }
      if (message === '/undo') {
        showError('Battle Lab confirma la jugada al completar ambos slots; deshacer después del envío no está disponible.');
        return;
      }
      return originalSend.apply(this, arguments);
    };
    room.__battleLabNativeControlsBridge = marker;
  }

  async function poll() {
    try {
      var healthResponse = await fetch(api + '/health', {cache: 'no-store'});
      if (!healthResponse.ok) return;
      var health = await healthResponse.json();
      activeSession = health && health.activeSession ? String(health.activeSession) : '';
      if (!activeSession) return;
      var sessionResponse = await fetch(api + '/sparring/' + encodeURIComponent(activeSession), {cache: 'no-store'});
      if (!sessionResponse.ok) return;
      var snapshot = await sessionResponse.json();
      if (snapshot && snapshot.battle && snapshot.battle.tag && roomid && snapshot.battle.tag !== roomid) return;
      lastSnapshot = snapshot;
      applyRequest(snapshot, false);
    } catch (_) {
      // War Room continues polling independently; keep the last native controls.
    }
  }

  frame.addEventListener('load', function () {
    setTimeout(function () { void poll(); }, 0);
  });
  setInterval(function () { void poll(); }, 300);
  void poll();
})();
</script>
</body>
</html>
'''


class BattleLabViewerHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "BattleLabShowdownViewer/1"

    def _path_only(self) -> str:
        return urlsplit(self.path).path

    def _serve_vendor_html(self, *, head_only: bool = False) -> None:
        original = self.path
        try:
            self.path = CLASSIC_PATH
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
        finally:
            self.path = original

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

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        path = self._path_only()
        if path == HEALTH_PATH:
            self._serve_health()
            return
        if path == VENDOR_ALIAS:
            self._serve_vendor_html()
            return
        if path == CLASSIC_PATH:
            if self.headers.get("User-Agent", "").startswith(VERIFY_USER_AGENT):
                self._serve_vendor_html()
            else:
                self._serve_bridge()
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib callback name
        path = self._path_only()
        if path == HEALTH_PATH:
            self._serve_health(head_only=True)
            return
        if path == VENDOR_ALIAS:
            self._serve_vendor_html(head_only=True)
            return
        if path == CLASSIC_PATH:
            if self.headers.get("User-Agent", "").startswith(VERIFY_USER_AGENT):
                self._serve_vendor_html(head_only=True)
            else:
                self._serve_bridge(head_only=True)
            return
        super().do_HEAD()


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
    handler = partial(BattleLabViewerHandler, directory=str(root))
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
