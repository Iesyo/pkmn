"use client";

import { useEffect, useRef } from "react";

import { WarRoomSparring as LocalWarRoomSparring } from "./war-room-sparring/index";
import type { TeamVersion } from "@/lib/types";
import type { WarRoomCorpusTeam } from "@/lib/war-room";

/**
 * Transport adapter for War Room Sparring.
 *
 * The Battle Lab implementation was originally loopback-only and therefore
 * emits a classic Showdown iframe that points at 127.0.0.1:8767/8766. That is
 * correct when the browser runs on the Battle Lab machine, but wrong when the
 * same Vite app is opened through its LAN URL from another computer: 127.0.0.1
 * would then refer to the remote browser machine.
 *
 * Keep the battle UI implementation untouched and adapt only its network
 * endpoint at the component boundary. The runtime's explicit LAN mode exposes
 * those two Showdown ports on the same host that serves the app, so replacing
 * only the host preserves every native Showdown path/hash and all Nana hooks.
 */
export function WarRoomSparring({
  team,
  corpusTeams,
}: {
  team: TeamVersion;
  corpusTeams: WarRoomCorpusTeam[];
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const rewriteViewer = () => {
      const host = window.location.hostname;
      if (!host || host === "127.0.0.1" || host === "localhost") return;

      for (const frame of root.querySelectorAll<HTMLIFrameElement>("iframe")) {
        const current = frame.getAttribute("src") || "";
        if (!current.includes("127.0.0.1:8767")) continue;
        const next = current
          .replace("http://127.0.0.1:8767", `http://${host}:8767`)
          .replace("~~127.0.0.1:8766", `~~${host}:8766`);
        if (next !== current) frame.setAttribute("src", next);
      }
    };

    rewriteViewer();
    const observer = new MutationObserver(rewriteViewer);
    observer.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["src"],
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={rootRef} className="contents">
      <LocalWarRoomSparring team={team} corpusTeams={corpusTeams} />
    </div>
  );
}
