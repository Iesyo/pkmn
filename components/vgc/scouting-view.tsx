"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, CircleCheck, Copy, ExternalLink, Microscope, Play, Radar, RefreshCw, ShieldQuestion, Swords } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { MatchRecord, ScoutingAnalysis, TeamGroup, TeamVersion } from "@/lib/types";

interface ScoutingCandidate {
  match: MatchRecord;
  version: TeamVersion;
}

function CandidateLabel({ candidate }: { candidate: ScoutingCandidate }) {
  return <>{candidate.match.opponentName} · {candidate.version.name} · {new Date(candidate.match.playedAt).toLocaleDateString("es-MX")}</>;
}

function AnalysisPokemon({ pokemon, analysis }: { pokemon: NonNullable<ScoutingAnalysis["result"]>["pokemon"][number]; analysis: ScoutingAnalysis }) {
  const inferences = analysis.result?.inferences.filter((entry) => entry.species === pokemon.species) ?? [];
  return (
    <article className="rounded-2xl border border-white/8 bg-slate-950/65 p-4">
      <div className="flex items-start gap-3">
        <div className="flex size-16 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/10 bg-cyan-300/5">
          <Image src={getSpriteUrl(pokemon.species)} alt={pokemon.species} width={58} height={58} unoptimized className="size-14 object-contain" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-black text-white">{pokemon.species}</h3>
            {pokemon.brought ? <Badge className="border-emerald-300/15 bg-emerald-300/8 text-emerald-200" variant="outline">Entró</Badge> : null}
          </div>
          <p className="mt-1 text-[10px] text-slate-500">{pokemon.item ? `@ ${pokemon.item}` : "Objeto no revelado"}</p>
          <p className="text-[10px] text-slate-500">{pokemon.ability ? `Ability: ${pokemon.ability}` : "Habilidad no revelada"}</p>
          {pokemon.teraType ? <p className="text-[10px] font-semibold text-cyan-300">Tera {pokemon.teraType}</p> : null}
        </div>
      </div>
      <div className="mt-3 flex min-h-7 flex-wrap gap-1.5">
        {pokemon.moves.length ? pokemon.moves.map((move) => <Badge key={move} variant="outline" className="border-white/8 bg-white/3 text-[9px] text-slate-300">{move}</Badge>) : <span className="text-[10px] text-slate-700">Sin movimientos revelados</span>}
      </div>
      <div className="mt-3 space-y-1.5 border-t border-white/7 pt-3">
        {inferences.length ? inferences.map((inference) => (
          <div key={inference.stat} className="flex items-center justify-between gap-3 rounded-xl bg-cyan-300/5 px-3 py-2 text-[10px]">
            <span className="font-bold text-cyan-200">{inference.stat} SP</span>
            <span className="font-mono font-black text-white">{inference.minimum === inference.maximum ? inference.minimum : `${inference.minimum}–${inference.maximum}`}</span>
          </div>
        )) : <div className="flex items-center gap-2 text-[10px] text-slate-600"><ShieldQuestion className="size-3.5" />Sin intervalo de stats demostrable</div>}
      </div>
    </article>
  );
}

export function ScoutingView({
  groups,
  initialMatchId,
  onJobStarted,
}: {
  groups: TeamGroup[];
  initialMatchId?: string;
  onJobStarted: (matchId: string) => void;
}) {
  const candidates = useMemo<ScoutingCandidate[]>(() => groups
    .flatMap((group) => group.versions.flatMap((version) => version.matches.map((match) => ({ match, version }))))
    .filter(({ match, version }) => Boolean(match.replayUrl) && !version.demo)
    .sort((left, right) => right.match.playedAt.localeCompare(left.match.playedAt)), [groups]);
  const [matchId, setMatchId] = useState(initialMatchId ?? candidates[0]?.match.id ?? "");
  const [analysis, setAnalysis] = useState<ScoutingAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [requestError, setRequestError] = useState("");
  const selected = candidates.find((candidate) => candidate.match.id === matchId) ?? candidates[0];

  useEffect(() => {
    if (!selected) return;
    let active = true;
    const load = async () => {
      const response = await fetch(`/api/scouting?matchId=${encodeURIComponent(selected.match.id)}`, { cache: "no-store" });
      const payload = (await response.json()) as { analysis?: ScoutingAnalysis | null };
      if (active && response.ok) setAnalysis(payload.analysis ?? null);
    };
    void load();
    const timer = window.setInterval(() => {
      if (analysis?.status === "queued" || analysis?.status === "running") void load();
    }, 900);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selected, analysis?.status]);

  async function startAnalysis() {
    if (!selected) return;
    setLoading(true);
    setRequestError("");
    try {
      const response = await fetch("/api/scouting", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ matchId: selected.match.id, action: "start" }),
      });
      const payload = (await response.json()) as { analysis?: ScoutingAnalysis; error?: string };
      if (!response.ok || !payload.analysis) throw new Error(payload.error || "No pudimos iniciar el análisis.");
      setAnalysis(payload.analysis);
      onJobStarted(selected.match.id);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "No pudimos iniciar el análisis.");
    } finally {
      setLoading(false);
    }
  }

  if (!selected) {
    return (
      <section className="rounded-[28px] border border-white/8 bg-slate-900/45 px-6 py-20 text-center">
        <Radar className="mx-auto size-10 text-slate-700" />
        <h1 className="mt-4 text-xl font-black text-white">Scouting rival</h1>
        <p className="mx-auto mt-2 max-w-lg text-sm text-slate-500">Guarda primero una partida con replay. Desde ese replay podremos extraer el equipo observado y acotar información oculta.</p>
      </section>
    );
  }

  const running = analysis?.status === "queued" || analysis?.status === "running";
  const result = analysis?.result;

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-cyan-300 via-violet-400 to-transparent" />
        <div className="flex flex-col gap-4 p-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex items-center gap-2"><Radar className="size-5 text-cyan-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-300">Laboratorio persistente</p></div>
            <h1 className="mt-2 text-2xl font-black tracking-tight text-white">Scouting rival</h1>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">Separa lo revelado por Showdown de lo que el motor de daño solamente puede acotar. Puedes cambiar de sección mientras trabaja.</p>
          </div>
          <div className="flex w-full flex-col gap-2 sm:flex-row lg:w-auto">
            <Select value={selected.match.id} onValueChange={(value) => { setMatchId(value); setAnalysis(null); setRequestError(""); }}>
              <SelectTrigger className="w-full border-white/10 bg-slate-950/70 sm:w-[360px]"><SelectValue /></SelectTrigger>
              <SelectContent className="border-white/10 bg-slate-950 text-slate-200">{candidates.map((candidate) => <SelectItem key={candidate.match.id} value={candidate.match.id}><CandidateLabel candidate={candidate} /></SelectItem>)}</SelectContent>
            </Select>
            <Button onClick={startAnalysis} disabled={loading || running} className="gap-2 bg-cyan-300 font-black text-slate-950 hover:bg-cyan-200">
              {loading || running ? <RefreshCw className="size-4 animate-spin" /> : <Play className="size-4" />}
              {analysis?.status === "complete" ? "Reanalizar" : running ? "Analizando" : "Analizar replay"}
            </Button>
          </div>
        </div>
        <div className="grid gap-3 border-t border-white/7 bg-slate-950/35 p-5 md:grid-cols-[1fr_auto] md:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-black text-white">{selected.match.opponentName}</span><span className="text-xs text-slate-600">vs.</span><span className="text-xs font-bold text-slate-300">{selected.version.name}</span><Badge variant="outline" className={selected.match.result === "win" ? "border-emerald-300/15 bg-emerald-300/7 text-emerald-200" : "border-rose-300/15 bg-rose-300/7 text-rose-200"}>{selected.match.result === "win" ? "WIN" : "LOSS"}</Badge></div>
            <a href={selected.match.replayUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-[10px] font-semibold text-cyan-300 hover:text-cyan-200">Abrir replay original <ExternalLink className="size-3" /></a>
          </div>
          <div className="flex items-center gap-2 text-[10px] text-slate-500">{analysis?.status === "complete" ? <CircleCheck className="size-4 text-emerald-300" /> : running ? <Microscope className="size-4 text-cyan-300" /> : <ShieldQuestion className="size-4" />}{analysis?.stage ?? "Sin analizar"}</div>
        </div>
      </section>

      {requestError ? <div className="flex items-center gap-2 rounded-xl border border-rose-300/15 bg-rose-300/7 px-4 py-3 text-xs text-rose-200"><AlertTriangle className="size-4" />{requestError}</div> : null}

      {analysis ? <section className="rounded-2xl border border-white/8 bg-slate-950/60 p-4">
        <div className="mb-2 flex items-center justify-between gap-3 text-[10px]"><span className="font-bold uppercase tracking-[0.14em] text-slate-500">Progreso real guardado</span><span className="font-mono font-black text-cyan-300">{analysis.progress}%</span></div>
        <Progress value={analysis.progress} className="h-2 bg-white/5" />
        {analysis.error ? <div className="mt-3 flex items-center gap-2 rounded-xl border border-rose-300/15 bg-rose-300/7 px-3 py-2 text-xs text-rose-200"><AlertTriangle className="size-4" />{analysis.error}</div> : null}
      </section> : null}

      {result ? <>
        <section>
          <div className="mb-3 flex items-center justify-between"><div><p className="text-[10px] font-black uppercase tracking-[0.16em] text-cyan-300">Equipo rival observado</p><h2 className="mt-1 text-lg font-black text-white">{result.opponentName}</h2></div><Badge variant="outline" className="border-emerald-300/15 bg-emerald-300/7 text-emerald-200"><Check className="mr-1 size-3" />Datos del log</Badge></div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{result.pokemon.map((pokemon) => <AnalysisPokemon key={pokemon.species} pokemon={pokemon} analysis={analysis} />)}</div>
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="rounded-2xl border border-white/8 bg-slate-950/60 p-4">
            <div className="flex items-center gap-2"><Swords className="size-4 text-violet-300" /><h2 className="text-xs font-black uppercase tracking-[0.14em] text-slate-300">Impactos usados por la calculadora</h2></div>
            <div className="mt-3 space-y-2">{result.observations.length ? result.observations.map((observation, index) => <div key={`${observation.turn}-${observation.move}-${index}`} className="grid gap-1 rounded-xl border border-white/7 bg-white/[0.02] px-3 py-2 text-[10px] sm:grid-cols-[48px_minmax(0,1fr)_90px] sm:items-center"><span className="font-mono text-slate-600">T{observation.turn}</span><span className="truncate font-semibold text-slate-300">{observation.attacker} · {observation.move} → {observation.defender}</span><span className="text-right font-mono font-black text-cyan-300">{observation.damagePercent}%</span></div>) : <p className="py-8 text-center text-xs text-slate-600">El replay no mostró daño directo utilizable.</p>}</div>
          </div>
          <div className="rounded-2xl border border-white/8 bg-slate-950/60 p-4">
            <div className="flex items-center justify-between gap-3"><div><p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Paste observado</p><p className="mt-1 text-xs text-slate-600">Parcial; nunca inventa campos ocultos.</p></div><Button type="button" variant="outline" size="sm" onClick={async () => { await navigator.clipboard.writeText(result.observedPaste); setCopied(true); }} className="gap-2 border-white/10 bg-white/3"><Copy className="size-3.5" />{copied ? "Copiado" : "Copiar"}</Button></div>
            <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-xl border border-white/7 bg-black/25 p-3 font-mono text-[10px] leading-5 text-slate-400">{result.observedPaste || "Sin información observable para construir el paste."}</pre>
          </div>
        </section>

        <section className="rounded-2xl border border-amber-300/10 bg-amber-300/[0.035] p-4"><p className="text-[10px] font-black uppercase tracking-[0.14em] text-amber-200">Límites del análisis</p><ul className="mt-2 space-y-1 text-[10px] leading-4 text-slate-500">{result.notices.map((notice) => <li key={notice}>• {notice}</li>)}</ul></section>
      </> : null}
    </div>
  );
}
