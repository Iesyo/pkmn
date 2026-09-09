# Pokémon Showdown damage calculator

Reproducible compiled snapshot of the official `smogon/damage-calc` package.

- Upstream: https://github.com/smogon/damage-calc
- Commit: `111407c919c2c886688db704ae97376e768b72e4`
- Scope: the `calc/` package compiled from TypeScript, including generation `0` (Pokémon Champions)
- Browser entry: `dist/browser-entry.mjs`, backed by an ESM bundle generated from the compiled package so Vite can load the engine without CommonJS cycle errors
- License: MIT; see `LICENSE`

The package is vendored because the current npm release tagged `0.11.0` predates the Champions implementation present on the official repository's default branch. Rebuild it from the pinned upstream commit before updating this snapshot. Regulation M-C ability data is resolved from the pinned Pokémon Showdown regulation source in the application while damage-calc catches up with Showdown's final Golisopod-Mega ability.
