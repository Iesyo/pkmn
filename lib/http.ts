import { DomainError } from "@/db/queries";
import { PasteValidationError } from "./paste";

export function apiError(error: unknown) {
  if (error instanceof DomainError || error instanceof PasteValidationError) {
    return Response.json({ error: error.message }, { status: error instanceof DomainError ? error.status : 400 });
  }

  const message = error instanceof Error ? error.message : "Error inesperado";
  const unavailable =
    message.includes("no such table") ||
    message.includes("base de datos") ||
    message.includes("D1");
  return Response.json(
    {
      error: unavailable
        ? "La persistencia todavía se está preparando. La interfaz sigue disponible en modo demostración."
        : "No pudimos completar la operación.",
    },
    { status: unavailable ? 503 : 500 },
  );
}
