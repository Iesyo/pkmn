"use client";

import { useEffect } from "react";

function legacyWriteText(text: string) {
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "0";
  textarea.style.top = "0";
  textarea.style.width = "1px";
  textarea.style.height = "1px";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);

  let copyEventSeen = false;
  const onCopy = (event: ClipboardEvent) => {
    copyEventSeen = true;
    if (!event.clipboardData) return;
    event.preventDefault();
    event.clipboardData.clearData();
    event.clipboardData.setData("text/plain", text);
  };

  document.addEventListener("copy", onCopy, true);
  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    document.removeEventListener("copy", onCopy, true);
    textarea.remove();
    previousFocus?.focus({ preventScroll: true });
  }

  if (!copied || !copyEventSeen) {
    window.prompt("No pude escribir al portapapeles automáticamente. Copia el paste con Ctrl+C:", text);
    return Promise.reject(new Error("El navegador bloqueó la escritura al portapapeles."));
  }

  return Promise.resolve();
}

export function ClipboardFallback() {
  useEffect(() => {
    if (window.isSecureContext && navigator.clipboard?.writeText) return;

    const fallback = { writeText: legacyWriteText };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: fallback,
      });
    } catch {
      try {
        Object.defineProperty(Object.getPrototypeOf(navigator), "clipboard", {
          configurable: true,
          get: () => fallback,
        });
      } catch {
        // Navigator can be locked down by the browser. Existing callers will then
        // preserve the browser's native behavior instead of breaking app startup.
      }
    }
  }, []);

  return null;
}
