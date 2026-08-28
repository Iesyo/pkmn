"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, BookOpen, Database, Flame, Hammer, Library, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";

import { LibraryCard } from "@/components/vgc/library-card";
import { TeamPanel } from "@/components/vgc/team-panel";
import { TeamSelector } from "@/components/vgc/team-selector";
import { TeamBuilder } from "@/components/vgc/team-builder";
import { AddTeamDialog, NewVersionDialog, ShowdownNamesDialog } from "@/components/vgc/team-dialogs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DEFAULT_LEFT_VERSION_ID, DEFAULT_RIGHT_VERSION_ID, DEMO_GROUPS } from "@/lib/demo-data";
import type { TeamGroup, TeamVersion } from "@/lib/types";
import { formatVersion } from "@/lib/team-builder";

type ConnectionState = "checking" | "ready" | "demo";

export function VgcDashboard() {
  const [storedGroups, setStoredGroups] = useState<TeamGroup[]>([]);
  const [showdownNames, setShowdownNames] = useState<string[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [activeView, setActiveView] = useState("compare");
  const [leftId, setLeftId] = useState(DEFAULT_LEFT_VERSION_ID);
  const [rightId, setRightId] = useState(DEFAULT_RIGHT_VERSION_ID);
  const [libraryTeamId, setLibraryTeamId] = useState(DEMO_GROUPS[0].id);
  const [libraryVersionId, setLibraryVersionId] = useState(DEFAULT_LEFT_VERSION_ID);
  const [builderVersionId, setBuilderVersionId] = useState(DEFAULT_LEFT_VERSION_ID);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/teams", { cache: "no-store" });
      const payload = (await response.json()) as { teams?: TeamGroup[] };
      if (!response.ok) throw new Error("Persistence unavailable");
      setStoredGroups(payload.teams ?? []);
      setConnection("ready");
    } catch {
      setConnection("demo");
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/teams", { cache: "no-store" }),
      fetch("/api/settings", { cache: "no-store" }),
    ])
      .then(async ([teamsResponse, settingsResponse]) => {
        const teamsPayload = (await teamsResponse.json()) as { teams?: TeamGroup[] };
        const settingsPayload = (await settingsResponse.json()) as { showdownNames?: string[] };
        if (!teamsResponse.ok || !settingsResponse.ok) throw new Error("Persistence unavailable");
        return { teams: teamsPayload.teams ?? [], showdownNames: settingsPayload.showdownNames ?? [] };
      })
      .then(({ teams, showdownNames: savedNames }) => {
        if (!active) return;
        setStoredGroups(teams);
        setShowdownNames(savedNames);
        setConnection("ready");
      })
      .catch(() => {
        if (active) setConnection("demo");
      });
    return () => {
      active = false;
    };
  }, []);

  const groups = useMemo(() => [...storedGroups, ...DEMO_GROUPS], [storedGroups]);
  const versions = useMemo(() => groups.flatMap((group) => group.versions), [groups]);
  const left = versions.find((version) => version.id === leftId) ?? versions[0];
  const right = versions.find((version) => version.id === rightId) ?? versions[1] ?? versions[0];
  const libraryTeam = groups.find((team) => team.id === libraryTeamId) ?? groups[0];
  const libraryVersion = libraryTeam?.versions.find((version) => version.id === libraryVersionId) ?? libraryTeam?.versions[0];

  function handleTeamCreated(team: TeamGroup) {
    setStoredGroups((current) => [team, ...current.filter((entry) => entry.id !== team.id)]);
    setLibraryTeamId(team.id);
    setLibraryVersionId(team.versions[0].id);
    setActiveView("library");
    setConnection("ready");
  }

  function handleVersionCreated(version: TeamVersion) {
    setStoredGroups((current) => current.map((team) => team.id === version.teamId ? { ...team, versions: [version, ...team.versions] } : team));
    setLibraryVersionId(version.id);
  }

  function handleBuilderTeamCreated(team: TeamGroup) {
    setStoredGroups((current) => [team, ...current.filter((entry) => entry.id !== team.id)]);
    setConnection("ready");
  }

  function selectLibraryTeam(team: TeamGroup) {
    setLibraryTeamId(team.id);
    setLibraryVersionId(team.versions[0]?.id ?? "");
  }

  function openInBuilder(version: TeamVersion) {
    setBuilderVersionId(version.id);
    setActiveView("builder");
  }

  return (
    <Tabs value={activeView} onValueChange={setActiveView} className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-white/7 bg-[#070b14]/88 backdrop-blur-2xl">
        <div className="mx-auto flex max-w-[1920px] items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <div className="relative flex size-10 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/20 bg-gradient-to-br from-cyan-300/20 to-violet-400/15 shadow-[0_0_30px_rgba(34,211,238,0.12)]"><Flame className="size-5 text-cyan-200" /><span className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full border-2 border-[#070b14] bg-emerald-400" /></div>
            <div className="min-w-0"><p className="truncate text-sm font-black tracking-[-0.02em] text-white sm:text-base">Like No One Ever Was</p><p className="text-[9px] font-bold uppercase tracking-[0.2em] text-slate-600">VGC Performance Lab</p></div>
          </div>

          <TabsList className="h-10 rounded-full border border-white/8 bg-white/4 p-1">
            <TabsTrigger value="compare" className="gap-2 rounded-full px-3 text-xs data-[state=active]:bg-white data-[state=active]:text-slate-950 sm:px-4"><ArrowLeftRight className="size-3.5" /><span className="hidden sm:inline">Comparador</span></TabsTrigger>
            <TabsTrigger value="library" className="gap-2 rounded-full px-3 text-xs data-[state=active]:bg-white data-[state=active]:text-slate-950 sm:px-4"><Library className="size-3.5" /><span className="hidden sm:inline">Teams</span></TabsTrigger>
            <TabsTrigger value="builder" className="gap-2 rounded-full px-3 text-xs data-[state=active]:bg-white data-[state=active]:text-slate-950 sm:px-4"><Hammer className="size-3.5" /><span className="hidden sm:inline">Team Builder</span></TabsTrigger>
          </TabsList>

          <div className="hidden items-center gap-2 lg:flex">
            <ShowdownNamesDialog names={showdownNames} onSaved={setShowdownNames} />
            <Badge variant="outline" className={connection === "ready" ? "gap-1.5 border-emerald-300/15 bg-emerald-300/7 text-emerald-300" : "gap-1.5 border-amber-300/15 bg-amber-300/7 text-amber-200"}>{connection === "checking" ? <RefreshCw className="size-3 animate-spin" /> : connection === "ready" ? <Database className="size-3" /> : <Sparkles className="size-3" />}{connection === "checking" ? "Conectando" : connection === "ready" ? "SQLite listo" : "Modo muestra"}</Badge>
            <AddTeamDialog onCreated={handleTeamCreated} />
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-[1920px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[520px] bg-[radial-gradient(circle_at_18%_12%,rgba(34,211,238,0.10),transparent_34%),radial-gradient(circle_at_82%_8%,rgba(217,70,239,0.09),transparent_34%)]" />
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div><div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.18em] text-cyan-300"><ShieldCheck className="size-3.5" />{activeView === "builder" ? "Construcción competitiva · versión controlada" : "Scouting personal · histórico real"}</div><h1 className="max-w-4xl text-3xl font-black tracking-[-0.045em] text-white sm:text-4xl">{activeView === "builder" ? "Construye el plan, conserva cada evolución." : "Compara decisiones, no predicciones."}</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">{activeView === "builder" ? "Edita seis sets, revisa la cobertura y deja que el sistema decida si corresponde v1.01 o v2." : "Dos equipos, sus versiones exactas y el rendimiento que ya ocurrió. Sin simulaciones ni cajas negras."}</p></div>
          <div className="flex flex-wrap gap-2 lg:hidden"><ShowdownNamesDialog names={showdownNames} onSaved={setShowdownNames} /><AddTeamDialog onCreated={handleTeamCreated} /></div>
        </div>

        <TabsContent value="compare" className="mt-0 outline-none">
          <section aria-label="Selección de equipos" className="relative mb-5 grid items-center gap-3 xl:grid-cols-[minmax(0,1fr)_72px_minmax(0,1fr)]">
            <TeamSelector label="Team A" value={left?.id ?? ""} versions={versions} onChange={setLeftId} accent="cyan" />
            <div className="mx-auto hidden size-14 items-center justify-center rounded-full border border-white/10 bg-slate-950 text-sm font-black italic tracking-tight text-white shadow-[0_0_35px_rgba(255,255,255,0.08)] xl:flex">VS</div>
            <TeamSelector label="Team B" value={right?.id ?? ""} versions={versions} onChange={setRightId} accent="violet" />
          </section>
          {left && right ? <div className="grid items-start gap-5 xl:grid-cols-2"><TeamPanel version={left} accent="cyan" onMatchCreated={refresh} /><TeamPanel version={right} accent="violet" onMatchCreated={refresh} /></div> : null}
        </TabsContent>

        <TabsContent value="library" className="mt-0 outline-none">
          <div className="grid items-start gap-5 lg:grid-cols-[310px_minmax(0,1fr)]">
            <aside className="rounded-[24px] border border-white/8 bg-slate-900/40 p-3 backdrop-blur-xl lg:sticky lg:top-24">
              <div className="flex items-center justify-between gap-2 px-1 pb-3"><div><h2 className="flex items-center gap-2 text-sm font-black text-white"><BookOpen className="size-4 text-cyan-300" />Teams</h2><p className="mt-1 text-[10px] text-slate-600">{groups.length} equipos · historial inmutable</p></div><AddTeamDialog onCreated={handleTeamCreated} /></div>
              <ScrollArea className="h-[calc(100vh-250px)] min-h-80 pr-2"><div className="space-y-2">{groups.map((team) => <LibraryCard key={team.id} team={team} selected={team.id === libraryTeam?.id} onClick={() => selectLibraryTeam(team)} />)}</div></ScrollArea>
            </aside>
            <div className="min-w-0">
              {libraryTeam && libraryVersion ? <><div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/8 bg-slate-950/60 p-3"><div><p className="text-[10px] font-black uppercase tracking-[0.15em] text-slate-500">Equipo seleccionado</p><p className="mt-1 text-sm font-bold text-white">{libraryTeam.name}</p></div><div className="flex flex-wrap items-center gap-2"><Select value={libraryVersion.id} onValueChange={setLibraryVersionId}><SelectTrigger className="w-36 border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent className="border-white/10 bg-slate-950 text-slate-200">{libraryTeam.versions.map((version) => <SelectItem key={version.id} value={version.id}>Versión {formatVersion(version)} · {version.games} G</SelectItem>)}</SelectContent></Select>{!libraryVersion.demo ? <NewVersionDialog team={libraryTeam} onCreated={handleVersionCreated} /> : <Button variant="outline" disabled className="rounded-full border-white/8 bg-white/3 text-slate-600">Ejemplo de v{formatVersion(libraryVersion)}</Button>}</div></div><TeamPanel version={libraryVersion} accent="cyan" onMatchCreated={refresh} extraAction={<Button variant="outline" onClick={() => openInBuilder(libraryVersion)} className="gap-2 rounded-full border-cyan-300/15 bg-cyan-300/5 text-cyan-100"><Hammer className="size-4" />Editar en Builder</Button>} /></> : null}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="builder" className="mt-0 outline-none">
          <TeamBuilder key={builderVersionId} groups={groups} initialVersion={versions.find((version) => version.id === builderVersionId) ?? left} onTeamCreated={handleBuilderTeamCreated} onVersionCreated={handleVersionCreated} />
        </TabsContent>
      </main>
      <footer className="border-t border-white/7 px-4 py-5 text-center text-[10px] text-slate-700">Like No One Ever Was · datos de tipos basados en un snapshot local de Pokémon Showdown · análisis descriptivo</footer>
    </Tabs>
  );
}
