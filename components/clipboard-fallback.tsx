"use client";

import { useEffect } from "react";

function legacyWriteText(text: string) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    textarea.remove();
  }

  if (!copied) {
    window.prompt("Copia manualmente el paste de la variante:", text);
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
        // The browser may lock Navigator. In that rare case existing callers keep
        // their native behavior instead of failing during app initialization.
      }
    }
  }, []);

  return null;
}
