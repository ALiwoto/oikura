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
