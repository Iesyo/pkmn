"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { WarRoomAutoLab } from "./war-room-auto-lab";
import { WarRoomSparring as LocalWarRoomSparring } from "./war-room-sparring/index";
import type { TeamVersion } from "@/lib/types";
import {
  WAR_ROOM_FORMAT_ID,
  isWarRoomCorpusResponse,
  type WarRoomCorpusTeam,
} from "@/lib/war-room";
import { cn } from "@/lib/utils";

/**
 * Transport adapter for War Room Sparring + Auto Lab.
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
 *
 * War Room is force-mounted so its working state survives navigation. That also
 * means its original corpus snapshot can outlive changes made in Mis pastes.
 * Sparring owns a live copy of the candidate corpus and refreshes it whenever
 * the persistent War Room tab becomes active again, or when the browser regains
 * focus. The regular War Room endpoint is used deliberately: private pastes are
 * read fresh while the public VGCPastes source keeps its normal server cache.
 */
export function WarRoomSparring({
  team,
  corpusTeams,
}: {
  team: TeamVersion;
  corpusTeams: WarRoomCorpusTeam[];
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const corpusRefreshController = useRef<AbortController | null>(null);
  const [liveCorpusTeams, setLiveCorpusTeams] = useState(corpusTeams);
  const [panel, setPanel] = useState<"sparring" | "auto-lab">("sparring");

  useEffect(() => {
    setLiveCorpusTeams(corpusTeams);
  }, [corpusTeams]);

  const refreshCorpus = useCallback(async () => {
    corpusRefreshController.current?.abort();
    const controller = new AbortController();
    corpusRefreshController.current = controller;

    try {
      const response = await fetch(`/api/war-room?format=${WAR_ROOM_FORMAT_ID}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) return;
      const payload = await response.json() as unknown;
      if (!controller.signal.aborted && isWarRoomCorpusResponse(payload)) {
        setLiveCorpusTeams(payload.teams);
      }
    } catch {
      // Keep the last known-good corpus when a refresh is cancelled or unavailable.
    } finally {
      if (corpusRefreshController.current === controller) {
        corpusRefreshController.current = null;
      }
    }
  }, []);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const hostPanel = root.closest<HTMLElement>('[role="tabpanel"]');
    const refreshWhenActive = () => {
      const inactive = hostPanel?.getAttribute("data-state") === "inactive" || Boolean(hostPanel?.hidden);
      if (!inactive) void refreshCorpus();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshWhenActive();
    };

    refreshWhenActive();
    const panelObserver = hostPanel ? new MutationObserver(refreshWhenActive) : null;
    panelObserver?.observe(hostPanel, {
      attributes: true,
      attributeFilter: ["data-state", "hidden"],
    });
    window.addEventListener("focus", refreshWhenActive);
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      panelObserver?.disconnect();
      window.removeEventListener("focus", refreshWhenActive);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      corpusRefreshController.current?.abort();
    };
  }, [refreshCorpus]);

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
    <div ref={rootRef} className="space-y-4">
      <div className="inline-flex rounded-2xl border border-white/8 bg-slate-950/45 p-1">
        <button
          type="button"
          onClick={() => setPanel("sparring")}
          className={cn(
            "rounded-xl px-4 py-2 text-[9px] font-black transition",
            panel === "sparring" ? "bg-cyan-300/12 text-cyan-100" : "text-slate-500 hover:text-slate-300",
          )}
        >
          Sparring
        </button>
        <button
          type="button"
          onClick={() => setPanel("auto-lab")}
          className={cn(
            "rounded-xl px-4 py-2 text-[9px] font-black transition",
            panel === "auto-lab" ? "bg-violet-300/12 text-violet-100" : "text-slate-500 hover:text-slate-300",
          )}
        >
          Auto Lab · Auditar + Optimizar
        </button>
      </div>

      {panel === "auto-lab"
        ? <WarRoomAutoLab team={team} corpusTeams={liveCorpusTeams} />
        : <div className="contents"><LocalWarRoomSparring team={team} corpusTeams={liveCorpusTeams} /></div>}
    </div>
  );
}
