# Rust Twitter renderer

This folder contains an optional Rust renderer for Twitter/X text screenshots.
The main Python bot will use it only when a compiled binary exists; otherwise it
keeps using the Python/Pillow renderer.

## Build

From this folder:

```bash
cargo build --release
```

That creates:

- Windows: `target/release/oikura-rust-render.exe`
- Linux/macOS: `target/release/oikura-rust-render`

No extra Python configuration is needed when the binary is in that default
location.

## Python integration

The bot checks for `rust_renderer/target/release/oikura-rust-render` when it
renders a Twitter/X text screenshot. On Windows it checks for the `.exe` file.
If the binary is missing, cannot start, exits, or returns an error response, the
bot falls back to the Python renderer for that screenshot.

Useful environment variables:

- `OIKURA_RUST_RENDER_BIN`: absolute or relative path to a renderer binary.
- `OIKURA_RUST_RENDER_PROFILE`: target profile to check under
  `rust_renderer/target`; defaults to `release`. Use `debug` for
  `target/debug`.

## Development notes

The Python side starts the binary with `--serve` and sends one JSON request per
line over stdin. The renderer writes one JSON response per line to stdout.

The renderer loads assets from the repository root, including:

- `assets/fonts`
- `assets/icons/checkmark.svg`
- `assets/icons/twitter_verified.png`

Keep stdout reserved for protocol responses. Use stderr for diagnostics.
