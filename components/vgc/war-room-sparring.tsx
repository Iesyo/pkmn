"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { Bot, Check, CircleAlert, Gamepad2, Loader2, RefreshCw, Swords } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { TeamVersion } from "@/lib/types";
import type { WarRoomCorpusTeam } from "@/lib/war-room";
import {
  inspectBattleReadyPaste,
  shuffledSparringCandidates,
  sparringCorpusCandidates,
} from "@/lib/war-room-sparring";
import { cn } from "@/lib/utils";

const LOCAL_SERVICE = "http://127.0.0.1:8765";

type LocalHealth = {
  ok: boolean;
  ready: boolean;
  checkpointExists: boolean;
  activeSession: string | null;
};

type SparringMon = {
  species: string;
  name: string;
  hp: number;
  fainted: boolean;
  status: string | null;
  item: string;
  ability: string;
  active: boolean;
};

type SingleAction = {
  kind: "move" | "switch" | "pass" | "other";
  value: string;
  label: string;
  target: number;
  flags: string[];
};

type LegalAction = {
  id: string;
  first: SingleAction;
  second: SingleAction;
};

type SparringSession = {
  id: string;
  phase: "starting" | "team-preview" | "waiting-choice" | "resolving" | "completed" | "error" | "cancelled";
  error: string;
  opponent: { id: string; label: string; source: string };
  battle: {
    tag?: string;
    turn?: number;
    finished?: boolean;
    won?: boolean;
    lost?: boolean;
    weather?: string[];
    fields?: string[];
    ownActive?: Array<SparringMon | null>;
    opponentActive?: Array<SparringMon | null>;
    ownTeam?: SparringMon[];
    opponentTeam?: SparringMon[];
  };
  actions: LegalAction[];
  result: null | {
    winner: "human" | "model" | "tie";
    turns: number;
    battleTag: string;
  };
  events: string[];
};

type LoadedOpponent = {
  team: WarRoomCorpusTeam;
  paste: string;
};

async function readPayload(response: Response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) as unknown : {};
  } catch {
    return {};
  }
}

function errorText(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    if ("detail" in payload && typeof payload.detail === "string") return payload.detail;
    if ("error" in payload && typeof payload.error === "string") return payload.error;
  }
  return fallback;
}

async function loadExactPaste(team: WarRoomCorpusTeam) {
  const response = team.savedPasteId
    ? await fetch(`/api/scouting-pastes/${encodeURIComponent(team.savedPasteId)}`, { cache: "no-store" })
    : await fetch("/api/pokepaste-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: team.pokepasteUrl }),
    });
  const payload = await readPayload(response);
  if (!response.ok) throw new Error(errorText(payload, "No pudimos cargar el paste."));
  const paste = team.savedPasteId
    && payload && typeof payload === "object"
    && "item" in payload && payload.item && typeof payload.item === "object"
    && "paste" in payload.item && typeof payload.item.paste === "string"
    ? payload.item.paste
    : payload && typeof payload === "object"
      && "paste" in payload && typeof payload.paste === "string"
      ? payload.paste
      : "";
  if (!paste) throw new Error("El paste llegó vacío.");
  return paste;
}

function actionKey(action: SingleAction) {
  return `${action.kind}|${action.value}|${action.target}|${action.flags.join(",")}`;
}

function uniqueActions(actions: LegalAction[], side: "first" | "second") {
  const seen = new Map<string, SingleAction>();
  for (const candidate of actions) {
    const action = candidate[side];
    seen.set(actionKey(action), action);
  }
  return [...seen.values()];
}

function PokemonBattleCard({ mon, opponent = false }: { mon: SparringMon | null | undefined; opponent?: boolean }) {
  if (!mon) return <div className="h-28 rounded-2xl border border-dashed border-white/8 bg-slate-950/30" />;
  return (
    <article className={cn(
      "relative flex min-h-28 items-center gap-3 rounded-2xl border p-3",
      mon.fainted ? "border-white/6 bg-slate-950/35 opacity-45" : opponent ? "border-rose-300/15 bg-rose-300/[0.035]" : "border-cyan-300/15 bg-cyan-300/[0.035]",
    )}>
      <Image src={getSpriteUrl(mon.species)} alt={mon.species} width={84} height={84} unoptimized className="size-20 shrink-0 object-contain" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <strong className="truncate text-sm text-white">{mon.species}</strong>
          {mon.status ? <Badge variant="outline" className="border-amber-300/15 text-[8px] text-amber-200">{mon.status}</Badge> : null}
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className={cn("h-full rounded-full transition-all", mon.hp > 50 ? "bg-emerald-400" : mon.hp > 20 ? "bg-amber-400" : "bg-rose-400")}
            style={{ width: `${Math.max(0, Math.min(100, mon.hp))}%` }}
          />
        </div>
        <p className="mt-1 font-mono text-[9px] text-slate-500">{Math.round(mon.hp)}% HP</p>
        <p className="mt-2 truncate text-[9px] text-slate-600">{[mon.item, mon.ability].filter(Boolean).join(" · ")}</p>
      </div>
    </article>
  );
}

export function WarRoomSparring({
  team,
  corpusTeams,
}: {
  team: TeamVersion;
  corpusTeams: WarRoomCorpusTeam[];
}) {
  const [health, setHealth] = useState<LocalHealth | null>(null);
  const [healthError, setHealthError] = useState("");
  const [opponent, setOpponent] = useState<LoadedOpponent | null>(null);
  const [session, setSession] = useState<SparringSession | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [rejected, setRejected] = useState(0);
  const [previewOrder, setPreviewOrder] = useState<number[]>([]);
  const [selectedFirst, setSelectedFirst] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  const ownReport = useMemo(() => inspectBattleReadyPaste(team.paste), [team.paste]);
  const candidates = useMemo(() => sparringCorpusCandidates(corpusTeams), [corpusTeams]);

  async function checkHealth() {
    try {
      const response = await fetch(`${LOCAL_SERVICE}/health`, { cache: "no-store" });
      const payload = await readPayload(response);
      if (!response.ok || !payload || typeof payload !== "object" || !("ok" in payload)) throw new Error("Respuesta inválida.");
      setHealth(payload as LocalHealth);
      setHealthError("");
    } catch {
      setHealth(null);
      setHealthError("Battle Lab local no responde en 127.0.0.1:8765.");
    }
  }

  useEffect(() => {
    void checkHealth();
  }, []);

  useEffect(() => {
    if (!session || ["completed", "error", "cancelled"].includes(session.phase)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}`, { cache: "no-store" });
        const payload = await readPayload(response);
        if (response.ok) setSession(payload as SparringSession);
      } catch {
        // Keep the last known battle state visible while loopback reconnects.
      }
    }, 350);
    return () => window.clearInterval(timer);
  }, [session?.id, session?.phase]);

  useEffect(() => {
    setSelectedFirst("");
  }, [session?.actions?.[0]?.id]);

  async function pickBattleReadyOpponent() {
    let skipped = 0;
    for (const candidate of shuffledSparringCandidates(corpusTeams)) {
      try {
        const paste = await loadExactPaste(candidate);
        if (!inspectBattleReadyPaste(paste).ready) {
          skipped += 1;
          continue;
        }
        setRejected(skipped);
        return { team: candidate, paste };
      } catch {
        skipped += 1;
      }
    }
    setRejected(skipped);
    throw new Error("No encontramos un rival con paste completo en VGCPastes o Mis pastes.");
  }

  async function startBattle() {
    if (!ownReport.ready) {
      setStartError(`Tu Team todavía no es battle-ready: ${ownReport.issues.slice(0, 3).join(" ")}`);
      return;
    }
    setStarting(true);
    setStartError("");
    setSession(null);
    setPreviewOrder([]);
    setOpponent(null);
    try {
      const picked = await pickBattleReadyOpponent();
      setOpponent(picked);
      const response = await fetch(`${LOCAL_SERVICE}/sparring`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          teamPaste: team.paste,
          opponentPaste: picked.paste,
          opponent: {
            id: picked.team.id,
            label: picked.team.playerName || picked.team.id,
            source: picked.team.source,
          },
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "Battle Lab rechazó la partida."));
      setSession(payload as SparringSession);
      await checkHealth();
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "No pudimos iniciar el Sparring.");
    } finally {
      setStarting(false);
    }
  }

  function togglePreview(slot: number) {
    setPreviewOrder((current) => {
      if (current.includes(slot)) return current.filter((value) => value !== slot);
      if (current.length >= 4) return current;
      return [...current, slot];
    });
  }

  async function submitPreview() {
    if (!session || previewOrder.length !== 4) return;
    setSubmitting(true);
    try {
      const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}/team-preview`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ order: previewOrder }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No pudimos enviar el Team Preview."));
      setSession(payload as SparringSession);
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "Falló Team Preview.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitAction(second: SingleAction) {
    if (!session || !selectedFirst) return;
    const candidate = session.actions.find((entry) =>
      actionKey(entry.first) === selectedFirst && actionKey(entry.second) === actionKey(second));
    if (!candidate) return;
    setSubmitting(true);
    try {
      const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}/choice`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ choiceId: candidate.id }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "Showdown rechazó la jugada."));
      setSession(payload as SparringSession);
      setSelectedFirst("");
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "No pudimos enviar la jugada.");
    } finally {
      setSubmitting(false);
    }
  }

  const firstActions = session?.phase === "waiting-choice" ? uniqueActions(session.actions, "first") : [];
  const compatible = selectedFirst && session
    ? session.actions.filter((entry) => actionKey(entry.first) === selectedFirst)
    : [];
  const secondActions = uniqueActions(compatible, "second");
  const resultLabel = session?.result?.winner === "human"
    ? "Victoria"
    : session?.result?.winner === "model"
      ? "Battle Lab gana"
      : "Empate";

  return (
    <div className="space-y-4">
      <section className="rounded-[24px] border border-cyan-300/12 bg-gradient-to-br from-cyan-300/[0.055] via-slate-900/55 to-violet-300/[0.04] p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Gamepad2 className="size-5 text-cyan-300" />
              <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Sparring interactivo</p>
              <Badge variant="outline" className={cn("text-[8px]", health ? "border-emerald-300/15 text-emerald-200" : "border-rose-300/15 text-rose-200")}>
                {health ? health.ready ? "Battle Lab listo" : "Loopback conectado" : "Servicio offline"}
              </Badge>
            </div>
            <h2 className="mt-2 text-xl font-black text-white">Tú contra LIGHT M-C</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
              Tu variante actual pelea contra un rival aleatorio de VGCPastes o Mis pastes. Solo entran pastes completos; no se infiere ningún campo.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => void checkHealth()} className="gap-2 border-white/10 bg-slate-950/45">
              <RefreshCw className="size-4" />Revisar servicio
            </Button>
            <Button type="button" onClick={() => void startBattle()} disabled={starting || !ownReport.ready || !candidates.length} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">
              {starting ? <Loader2 className="size-4 animate-spin" /> : <Swords className="size-4" />}
              {session ? "Nuevo rival" : "Buscar rival y pelear"}
            </Button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2 text-[9px]">
          <Badge variant="outline" className={ownReport.ready ? "border-emerald-300/15 text-emerald-200" : "border-rose-300/15 text-rose-200"}>
            {ownReport.ready ? "Tu Team · completo" : `Tu Team · ${ownReport.issues.length} huecos`}
          </Badge>
          <Badge variant="outline" className="border-white/8 text-slate-400">{candidates.length} candidatos con fuente exacta</Badge>
          {rejected ? <Badge variant="outline" className="border-amber-300/15 text-amber-200">{rejected} incompletos saltados</Badge> : null}
          {opponent ? <Badge variant="outline" className="border-violet-300/15 text-violet-200">Rival: {opponent.team.playerName || opponent.team.id}</Badge> : null}
        </div>
        {healthError ? <p className="mt-3 flex items-center gap-2 text-[10px] text-rose-200"><CircleAlert className="size-3.5" />{healthError}</p> : null}
        {startError ? <p className="mt-2 flex items-start gap-2 rounded-xl border border-rose-300/12 bg-rose-300/[0.04] px-3 py-2 text-[10px] leading-4 text-rose-100"><CircleAlert className="mt-0.5 size-3.5 shrink-0" />{startError}</p> : null}
      </section>

      {session?.phase === "team-preview" ? (
        <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
          <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Team Preview</p>
          <h2 className="mt-1 text-lg font-black text-white">Elige tus cuatro en orden</h2>
          <p className="mt-1 text-[10px] text-slate-600">1-2 serán el lead; 3-4 la backline. Battle Lab elige los suyos por su cuenta.</p>
          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {team.pokemon.map((set, index) => {
              const slot = index + 1;
              const order = previewOrder.indexOf(slot);
              return (
                <button key={set.id} type="button" onClick={() => togglePreview(slot)} className={cn(
                  "flex items-center gap-3 rounded-2xl border p-3 text-left transition",
                  order >= 0 ? "border-cyan-300/30 bg-cyan-300/[0.07]" : "border-white/7 bg-slate-950/45 hover:border-white/15",
                )}>
                  <div className="relative">
                    <Image src={getSpriteUrl(set.species)} alt={set.species} width={56} height={56} unoptimized className="size-14 object-contain" />
                    {order >= 0 ? <span className="absolute -right-1 -top-1 flex size-5 items-center justify-center rounded-full bg-cyan-300 text-[9px] font-black text-slate-950">{order + 1}</span> : null}
                  </div>
                  <div className="min-w-0"><strong className="block truncate text-xs text-white">{set.species}</strong><span className="mt-1 block truncate text-[9px] text-slate-600">{set.item}</span></div>
                </button>
              );
            })}
          </div>
          <Button type="button" onClick={() => void submitPreview()} disabled={previewOrder.length !== 4 || submitting} className="mt-4 gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">
            {submitting ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}Confirmar Bring 4
          </Button>
        </section>
      ) : null}

      {session && session.phase !== "starting" && session.phase !== "team-preview" ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <section className="overflow-hidden rounded-[24px] border border-white/8 bg-slate-900/45">
            <div className="border-b border-white/7 bg-slate-950/35 px-5 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-[10px] font-bold text-rose-200"><Bot className="size-4" />Battle Lab · {session.opponent.label || session.opponent.id}</span>
                <span className="font-mono text-[10px] text-slate-500">Turno {session.battle.turn ?? 0}</span>
              </div>
            </div>

            <div className="space-y-3 bg-[radial-gradient(circle_at_50%_45%,rgba(34,211,238,0.06),transparent_44%)] p-5">
              <div className="grid gap-3 sm:grid-cols-2">
                {(session.battle.opponentActive ?? [null, null]).map((mon, index) => <PokemonBattleCard key={`opp-${index}`} mon={mon} opponent />)}
              </div>
              <div className="flex items-center justify-center py-1"><Swords className="size-5 text-slate-700" /></div>
              <div className="grid gap-3 sm:grid-cols-2">
                {(session.battle.ownActive ?? [null, null]).map((mon, index) => <PokemonBattleCard key={`own-${index}`} mon={mon} />)}
              </div>
              {session.battle.weather?.length || session.battle.fields?.length ? <p className="text-center text-[9px] text-slate-600">{[...(session.battle.weather ?? []), ...(session.battle.fields ?? [])].join(" · ")}</p> : null}
            </div>

            <div className="border-t border-white/7 p-5">
              {session.phase === "waiting-choice" ? (
                <div>
                  <p className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300">Pokémon 1</p>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    {firstActions.map((action) => {
                      const key = actionKey(action);
                      return <button key={key} type="button" onClick={() => setSelectedFirst(key)} className={cn("rounded-xl border px-3 py-2.5 text-left text-[10px] transition", selectedFirst === key ? "border-cyan-300/30 bg-cyan-300/[0.08] text-cyan-100" : "border-white/8 bg-slate-950/45 text-slate-300 hover:border-white/15")}>{action.label}</button>;
                    })}
                  </div>
                  {selectedFirst ? (
                    <>
                      <p className="mt-4 text-[9px] font-black uppercase tracking-[0.14em] text-violet-300">Pokémon 2</p>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        {secondActions.map((action) => <button key={actionKey(action)} type="button" disabled={submitting} onClick={() => void submitAction(action)} className="rounded-xl border border-violet-300/15 bg-violet-300/[0.045] px-3 py-2.5 text-left text-[10px] text-violet-100 transition hover:border-violet-300/30">{action.label}</button>)}
                      </div>
                    </>
                  ) : null}
                  <p className="mt-3 text-[9px] text-slate-600">Las opciones vienen directamente de `battle.valid_orders`; la UI no fabrica movimientos, targets ni switches.</p>
                </div>
              ) : session.phase === "resolving" ? (
                <div className="flex items-center justify-center gap-2 py-8 text-xs text-slate-400"><Loader2 className="size-4 animate-spin text-cyan-300" />Battle Lab y Showdown están resolviendo el turno…</div>
              ) : session.phase === "completed" ? (
                <div className="py-6 text-center">
                  <p className={cn("text-2xl font-black", session.result?.winner === "human" ? "text-emerald-300" : session.result?.winner === "model" ? "text-rose-300" : "text-amber-300")}>{resultLabel}</p>
                  <p className="mt-2 text-xs text-slate-500">{session.result?.turns} turnos · {session.result?.battleTag}</p>
                </div>
              ) : session.phase === "error" ? (
                <div className="rounded-xl border border-rose-300/12 bg-rose-300/[0.04] px-4 py-3 text-xs text-rose-100">{session.error}</div>
              ) : null}
            </div>
          </section>

          <aside className="rounded-[24px] border border-white/8 bg-slate-900/45 p-4">
            <p className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Battle log</p>
            <div className="mt-3 max-h-[560px] space-y-2 overflow-y-auto pr-1">
              {session.events.map((event, index) => <div key={`${index}-${event}`} className="rounded-xl border border-white/6 bg-slate-950/40 px-3 py-2 text-[9px] leading-4 text-slate-500">{event}</div>)}
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
