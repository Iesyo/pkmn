import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Like No One Ever Was · VGC Performance Lab",
  description:
    "Teams y comparador personal de rendimiento histórico para equipos Pokémon VGC.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es" className="dark">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
