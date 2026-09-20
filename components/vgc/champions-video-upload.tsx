"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, FileVideo2, Loader2, UploadCloud } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import type { TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";

const CHUNK_BYTES = 8 * 1024 * 1024;
const ACCEPTED_VIDEO_TYPES = ".mp4,.mkv,.mov,.webm,video/mp4,video/webm,video/quicktime";

type ChampionsJobStatus = "uploading" | "queued" | "analyzing" | "ready" | "error";

interface ChampionsVideoJob {
  id: string;
  teamVersionId: string;
  filename: string;
  sizeBytes: number;
  uploadedBytes: number;
  status: ChampionsJobStatus;
  stage: string;
  progress: number;
  processedFrames: number;
  totalFrames: number | null;
  elapsedSeconds: number;
  etaSeconds: number | null;
  eventsDetected: number;
  battlesDetected: number;
  skippedFrames: number;
  warnings: string[];
  replayCount: number;
  error: string | null;
  createdAt: string;
  updatedAt: string;
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as T & { detail?: string; error?: string };
  if (!response.ok) throw new Error(payload.detail || payload.error || "No pudimos completar la operación.");
  return payload;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 ** 2) return `${Math.max(0, bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}

function formatDuration(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainder = rounded % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function championsContext(version: TeamVersion) {
  const aliases = Object.fromEntries(
    version.pokemon
      .filter((pokemon) => pokemon.nickname.trim() && pokemon.nickname.trim().toLocaleLowerCase() !== pokemon.species.toLocaleLowerCase())
      .map((pokemon) => [pokemon.nickname.trim(), pokemon.species]),
  );
  return {
    players: { p1: "Player", p2: "Rival" },
    teams: { p1: version.pokemon.map((pokemon) => pokemon.species), p2: [] },
    aliases: { p1: aliases, p2: {} },
    language: "en",
    format: "gen9championsvgc2026regmc",
  };
}

function statusTone(status: ChampionsJobStatus) {
  if (status === "ready") return "border-emerald-300/20 bg-emerald-300/8 text-emerald-200";
  if (status === "error") return "border-rose-300/20 bg-rose-300/8 text-rose-200";
  if (status === "uploading") return "border-cyan-300/20 bg-cyan-300/8 text-cyan-200";
  return "border-amber-300/20 bg-amber-300/8 text-amber-200";
}

export function ChampionsVideoUpload({
  version,
  disabled = false,
  onReplayReady,
}: {
  version: TeamVersion;
  disabled?: boolean;
  onReplayReady: (replay: unknown) => Promise<void>;
}) {
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<ChampionsVideoJob[]>([]);
  const [currentJobId, setCurrentJobId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [readingReplay, setReadingReplay] = useState<number | null>(null);
  const [error, setError] = useState("");
  const currentJob = useMemo(
    () => jobs.find((job) => job.id === currentJobId) ?? jobs[0] ?? null,
    [currentJobId, jobs],
  );

  function mergeJob(job: ChampionsVideoJob) {
    setJobs((current) => [job, ...current.filter((entry) => entry.id !== job.id)]);
    setCurrentJobId(job.id);
  }

  useEffect(() => {
    if (!open) return;
    let active = true;
    const params = new URLSearchParams({ team_version_id: version.id });
    fetch(`/api/champions-jobs/jobs?${params.toString()}`, { cache: "no-store" })
      .then((response) => readJson<{ jobs: ChampionsVideoJob[] }>(response))
      .then((payload) => {
        if (!active) return;
        setJobs(payload.jobs);
        setCurrentJobId(payload.jobs[0]?.id || "");
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : "No pudimos conectar con la cola Champions.");
      });
    return () => {
      active = false;
    };
  }, [open, version.id]);

  useEffect(() => {
    if (!open || !currentJob || !["queued", "analyzing"].includes(currentJob.status)) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const payload = await readJson<{ job: ChampionsVideoJob }>(
          await fetch(`/api/champions-jobs/jobs/${currentJob.id}`, { cache: "no-store" }),
        );
        if (active) mergeJob(payload.job);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Perdimos conexión con la cola Champions.");
      }
    }, 1_000);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [currentJob, open]);

  async function sendChunk(jobId: string, offset: number, chunk: Blob) {
    let lastError: unknown;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await readJson<{ job: ChampionsVideoJob }>(
          await fetch(`/api/champions-jobs/jobs/${jobId}/chunks?offset=${offset}`, {
            method: "PUT",
            headers: { "content-type": "application/octet-stream" },
            body: chunk,
          }),
        );
      } catch (caught) {
        lastError = caught;
        if (attempt < 2) await new Promise((resolveDelay) => window.setTimeout(resolveDelay, 500 * (attempt + 1)));
      }
    }
    throw lastError;
  }

  async function uploadVideo(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file || uploading) return;
    setUploading(true);
    setError("");
    try {
      const resumableJob = jobs.find((job) =>
        job.status === "uploading"
        && job.filename === file.name
        && job.sizeBytes === file.size
      );
      let job = resumableJob;
      if (!job) {
        const created = await readJson<{ job: ChampionsVideoJob }>(
          await fetch("/api/champions-jobs/jobs", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              sizeBytes: file.size,
              teamVersionId: version.id,
              context: championsContext(version),
              sampleFps: 2,
              maxBattles: 0,
            }),
          }),
        );
        job = created.job;
      }
      mergeJob(job);
      for (let offset = job.uploadedBytes; offset < file.size; offset = job.uploadedBytes) {
        const payload = await sendChunk(job.id, offset, file.slice(offset, Math.min(file.size, offset + CHUNK_BYTES)));
        job = payload.job;
        mergeJob(job);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos subir el vídeo.");
    } finally {
      setUploading(false);
    }
  }

  async function reviewReplay(replayNumber: number) {
    if (!currentJob) return;
    setReadingReplay(replayNumber);
    setError("");
    try {
      const payload = await readJson<{ replay: unknown }>(
        await fetch(`/api/champions-jobs/jobs/${currentJob.id}/replays/${replayNumber}`, { cache: "no-store" }),
      );
      await onReplayReady(payload.replay);
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos abrir el replay generado.");
    } finally {
      setReadingReplay(null);
    }
  }

  const progress = Math.round((currentJob?.progress ?? 0) * 100);

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) setError("");
      }}
    >
      <DialogTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-md border border-cyan-300/15 bg-cyan-300/5 px-2 py-1 font-bold text-cyan-200 transition hover:bg-cyan-300/10",
            disabled && "pointer-events-none opacity-40",
          )}
        >
          <FileVideo2 className="size-3" />Vídeo Champions
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-[#070b14] text-slate-100 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><UploadCloud className="size-5 text-cyan-300" />Importar vídeo desde otra PC</DialogTitle>
          <DialogDescription className="text-slate-500">
            La carga queda en la ROG y continúa procesándose aunque cierres esta ventana. Cada batalla genera un replay independiente para revisión.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="rounded-2xl border border-dashed border-cyan-300/20 bg-cyan-300/[0.035] p-4">
            <input
              id={inputId}
              type="file"
              accept={ACCEPTED_VIDEO_TYPES}
              onChange={uploadVideo}
              disabled={uploading}
              className="sr-only"
            />
            <label htmlFor={inputId} className={cn("flex cursor-pointer items-center justify-between gap-4", uploading && "pointer-events-none opacity-60")}>
              <span>
                <span className="block text-sm font-black text-cyan-100">Seleccionar grabación</span>
                <span className="mt-1 block text-[10px] text-slate-500">MP4, MKV, MOV o WebM · carga reanudable en fragmentos de 8 MiB</span>
              </span>
              <span className="inline-flex h-10 items-center gap-2 rounded-xl bg-cyan-300 px-4 text-xs font-black text-slate-950">
                {uploading ? <Loader2 className="size-4 animate-spin" /> : <UploadCloud className="size-4" />}
                {uploading ? "Subiendo" : "Elegir vídeo"}
              </span>
            </label>
          </div>

          {jobs.length > 1 ? (
            <div className="flex flex-wrap gap-1.5">
              {jobs.slice(0, 6).map((job) => (
                <Button
                  key={job.id}
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setCurrentJobId(job.id)}
                  className={cn("h-7 max-w-40 truncate border-white/10 px-2 text-[9px]", job.id === currentJob?.id && "border-cyan-300/30 bg-cyan-300/8 text-cyan-100")}
                >
                  {job.filename}
                </Button>
              ))}
            </div>
          ) : null}

          {currentJob ? (
            <section className="grid gap-3 rounded-2xl border border-white/8 bg-white/[0.025] p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-black text-white">{currentJob.filename}</p>
                  <p className="mt-1 text-[10px] text-slate-500">
                    {formatBytes(currentJob.uploadedBytes)} / {formatBytes(currentJob.sizeBytes)}
                  </p>
                </div>
                <Badge variant="outline" className={cn("shrink-0 text-[9px]", statusTone(currentJob.status))}>{currentJob.stage}</Badge>
              </div>
              <Progress value={progress} className="h-2 bg-white/8 [&_[data-slot=progress-indicator]]:bg-cyan-300" />
              <div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4">
                <div><p className="text-slate-600">Avance</p><p className="mt-0.5 font-mono text-slate-300">{progress}%</p></div>
                <div><p className="text-slate-600">Frames</p><p className="mt-0.5 font-mono text-slate-300">{currentJob.processedFrames}{currentJob.totalFrames ? `/${currentJob.totalFrames}` : ""}</p></div>
                <div><p className="text-slate-600">ETA</p><p className="mt-0.5 font-mono text-slate-300">{formatDuration(currentJob.etaSeconds)}</p></div>
                <div><p className="text-slate-600">Partidas</p><p className="mt-0.5 font-mono text-slate-300">{currentJob.battlesDetected}</p></div>
              </div>
              {currentJob.status === "analyzing" || currentJob.status === "queued" ? (
                <p className="flex items-center gap-2 text-[10px] text-amber-200"><Loader2 className="size-3 animate-spin" />Puedes cerrar esta ventana; la cola seguirá trabajando en la ROG.</p>
              ) : null}
              {currentJob.status === "ready" ? (
                <div className="grid gap-2 rounded-xl border border-emerald-300/15 bg-emerald-300/[0.045] p-3">
                  <p className="flex items-center gap-2 text-xs font-bold text-emerald-200"><CheckCircle2 className="size-4" />{currentJob.replayCount} replay{currentJob.replayCount === 1 ? "" : "s"} listo{currentJob.replayCount === 1 ? "" : "s"}</p>
                  <div className="flex flex-wrap gap-2">
                    {Array.from({ length: currentJob.replayCount }, (_, index) => index + 1).map((replayNumber) => (
                      <Button
                        key={replayNumber}
                        type="button"
                        size="sm"
                        onClick={() => void reviewReplay(replayNumber)}
                        disabled={readingReplay !== null}
                        className="gap-1.5 bg-emerald-300 font-black text-slate-950 hover:bg-emerald-200"
                      >
                        {readingReplay === replayNumber ? <Loader2 className="size-3 animate-spin" /> : null}
                        Revisar partida {replayNumber}
                      </Button>
                    ))}
                  </div>
                  <p className="text-[9px] text-slate-500">Revisar abre el registro existente; la partida sólo se guarda cuando confirmas sus datos.</p>
                </div>
              ) : null}
              {currentJob.status === "error" ? (
                <p className="flex items-start gap-2 rounded-xl border border-rose-300/15 bg-rose-300/6 p-3 text-[10px] text-rose-200"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{currentJob.error || "El procesamiento terminó con error."}</p>
              ) : null}
            </section>
          ) : (
            <p className="rounded-xl border border-white/7 bg-white/[0.02] px-3 py-6 text-center text-xs text-slate-600">Todavía no hay vídeos asociados a esta versión del Team.</p>
          )}

          {error ? <p role="alert" className="flex items-start gap-2 rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{error}</p> : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
