export async function getDatabase(): Promise<D1Database> {
  const { env } = await import("cloudflare:workers");
  if (!env.DB) {
    throw new Error(
      "La base de datos todavía no está disponible. Publica las migraciones D1 antes de guardar equipos.",
    );
  }

  return env.DB;
}
