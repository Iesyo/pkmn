"use client";

import { useId, useState } from "react";
import { FileJson2, Gamepad2, Link2, Loader2, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ImportedReplayMatch } from "@/lib/showdown-replay";
import type { TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ChampionsQuickMatchDialog } from "./champions-quick-match";
import { AddMatchDialog } from "./team-dialogs";

async function readResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || "No pudimos leer el replay.");
  return payload;
}

export function MatchQuickEntry({ version, onCreated }: { version: TeamVersion; onCreated?: () => void }) {
  const replayInputId = useId();
  const reconstructedReplayInputId = useId();
  const [replayUrl, setReplayUrl] = useState("");
  const [replayDialogOpen, setReplayDialogOpen] = useState(false);
  const [championsDialogOpen, setChampionsDialogOpen] = useState(false);
  const [reading, setReading] = useState(false);
  const [importedReplay, setImportedReplay] = useState<ImportedReplayMatch | null>(null);
  const [error, setError] = useState("");

  const disabled = version.demo || !onCreated;
  const hasReplayInput = replayUrl.trim().length > 0;
  const championsMode = version.format === "champions" && !hasReplayInput;

  async function readReplay(source: { replayUrl: string } | { replay: unknown }) {
    const payload = await readResponse<{ match: ImportedReplayMatch }>(
      await fetch("/api/replays", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...source, teamSpecies: version.pokemon.map((pokemon) => pokemon.species) }),
      }),
    );
    setImportedReplay(payload.match);
    setReplayDialogOpen(true);
  }

  async function continueEntry(event: React.FormEvent) {
    event.preventDefault();
    setError("");

    if (!hasReplayInput) {
      if (version.format === "champions") setChampionsDialogOpen(true);
      return;
    }

    setReading(true);
    try {
      await readReplay({ replayUrl: replayUrl.trim() });
    } catch (caught) {
      setImportedReplay(null);
      setError(caught instanceof Error ? caught.message : "No pudimos leer el replay.");
    } finally {
      setReading(false);
    }
  }

  async function importReconstructedReplay(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;
    setError("");
    setReading(true);
    try {
      if (file.size > 5_000_000) throw new Error("El replay reconstruido excede 5 MB.");
      const replay = JSON.parse(await file.text()) as unknown;
      await readReplay({ replay });
    } catch (caught) {
      setImportedReplay(null);
      setError(caught instanceof SyntaxError
        ? "El archivo no contiene un replay JSON válido."
        : caught instanceof Error ? caught.message : "No pudimos leer el replay reconstruido.");
    } finally {
      setReading(false);
    }
  }

  return (
    <div className="w-full">
      <form onSubmit={continueEntry} className="grid gap-3 rounded-2xl border border-cyan-300/20 bg-cyan-300/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] sm:grid-cols-[minmax(0,1fr)_auto]">
        <div className="grid gap-1.5">
          <Label htmlFor={replayInputId} className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-100">URL del replay de Showdown</Label>
          <div className="relative">
            <span className="pointer-events-none absolute inset-y-1.5 left-1.5 flex w-9 items-center justify-center rounded-lg border border-cyan-300/15 bg-cyan-300/8">
              <Link2 className="size-4 text-cyan-300" />
            </span>
            <Input
              id={replayInputId}
              type="url"
              value={replayUrl}
              onChange={(event) => {
                setReplayUrl(event.target.value);
                setImportedReplay(null);
                setError("");
              }}
              disabled={disabled}
              placeholder="https://replay.pokemonshowdown.com/gen9vgc..."
              className="replay-url-field h-12 border-cyan-300/35 bg-[#101c30] pl-12 font-mono text-xs text-white placeholder:text-slate-500"
            />
          </div>
        </div>
        <Button
          type="submit"
          disabled={disabled || reading || (!hasReplayInput && version.format !== "champions")}
          className={cn(
            "h-12 gap-2 self-end px-5 font-black text-slate-950",
            championsMode
              ? "bg-amber-300 shadow-[0_8px_24px_rgba(252,211,77,0.12)] hover:bg-amber-200"
              : "bg-cyan-300 shadow-[0_8px_24px_rgba(34,211,238,0.14)] hover:bg-cyan-200",
          )}
        >
          {reading ? <Loader2 className="size-4 animate-spin" /> : championsMode ? <Gamepad2 className="size-4" /> : <Plus className="size-4" />}
          {reading ? "Leyendo replay" : championsMode ? "Partida Champions" : "Agregar replay"}
        </Button>
      </form>
      <div className="mt-1.5 flex items-center justify-between gap-3 text-[9px]">
        <div className="flex min-w-0 items-center gap-2">
          <span className={error ? "text-rose-300" : "text-slate-600"}>
            {error || (disabled
              ? "Guarda un equipo real para habilitar el registro."
              : championsMode
                ? "Sin enlace abre el registro rápido; también puedes cargar un replay reconstruido."
                : "Resultado, rival, Team Preview, picks y leads se importan automáticamente.")}
          </span>
          {version.format === "champions" ? (
            <>
              <input
                id={reconstructedReplayInputId}
                type="file"
                accept=".json,application/json"
                onChange={importReconstructedReplay}
                disabled={disabled || reading}
                className="sr-only"
              />
              <label
                htmlFor={reconstructedReplayInputId}
                className={cn(
                  "inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-md border border-amber-300/15 bg-amber-300/5 px-2 py-1 font-bold text-amber-200 transition hover:bg-amber-300/10",
                  (disabled || reading) && "pointer-events-none opacity-40",
                )}
              >
                <FileJson2 className="size-3" />Replay Champions
              </label>
            </>
          ) : null}
        </div>
        <span className="shrink-0 text-slate-700">{championsMode ? "Champions" : "Showdown"}</span>
      </div>

      {version.format === "champions" ? (
        <ChampionsQuickMatchDialog
          key={championsDialogOpen ? "champions-open" : "champions-closed"}
          version={version}
          onCreated={onCreated}
          open={championsDialogOpen}
          onOpenChange={setChampionsDialogOpen}
          hideTrigger
        />
      ) : null}

      {importedReplay ? (
        <AddMatchDialog
          key={`${importedReplay.replayUrl || "champions-reconstructed"}-${importedReplay.playedAt ?? "undated"}-${importedReplay.opponentName}`}
          version={version}
          onCreated={() => {
            setReplayUrl("");
            setImportedReplay(null);
            onCreated?.();
          }}
          open={replayDialogOpen}
          onOpenChange={setReplayDialogOpen}
          initialReplay={importedReplay}
          hideTrigger
        />
      ) : null}
    </div>
  );
}
