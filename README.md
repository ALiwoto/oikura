# oikura

A Telegram MTProto bot for expanding Twitter/X and Pixiv links in whitelisted chats.

Copy `config.sample.ini` to `config.ini`, fill in the values, then run:

```bash
python -m oikura
```

Use another config path with:

```bash
python -m oikura -c config.ini
```

The configured `owner_id` can update chats from Telegram:

```text
/whitelist <chat_id>
/rmwhitelist <chat_id>
```

Docker:

```bash
docker compose up -d --build
```

## Optional Rust Twitter renderer

Twitter/X text screenshots can use an optional Rust renderer. If a compiled
binary exists at `rust_renderer/target/release/oikura-rust-render` (`.exe` on
Windows), oikura will try it first and fall back to the Python renderer if it is
missing or returns an error.

To build the optional renderer:

```bash
cd rust_renderer
cargo build --release
```

You can override the binary path with `OIKURA_RUST_RENDER_BIN`, or use
`OIKURA_RUST_RENDER_PROFILE=debug` to look under `rust_renderer/target/debug`.
See `rust_renderer/README.md` for details.
