# Pokémon Showdown damage calculator

Reproducible compiled snapshot of the official `smogon/damage-calc` package.

- Upstream: https://github.com/smogon/damage-calc
- Commit: `2c50a89d9e369289965b1448a6f5c1b7d41520c7`
- Scope: the `calc/` package compiled from TypeScript, including generation `0` (Pokémon Champions)
- Browser entry: `dist/browser-entry.mjs`, backed by an ESM bundle generated from the compiled package so Vite can load the engine without CommonJS cycle errors
- License: MIT; see `LICENSE`

The package is vendored because the current npm release tagged `0.11.0` predates the Champions implementation present on the official repository's default branch. Rebuild it from the pinned upstream commit before updating this snapshot.
