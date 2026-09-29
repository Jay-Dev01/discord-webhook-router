# Discord address router

Watches one Discord text channel for incoming webhook messages. Reads the
`Delivered To` embed field and copies the message content, embeds, and attachments
to the channel configured for that address in the same server. Copies appear
under your bot's name. The original message stays in the intake channel.

## Setup (Windows / PowerShell)

1. Create an application at <https://discord.com/developers/applications>.
   On its **Bot** page, enable **Message Content Intent** and obtain a bot token.
   Keep the token in your local `.env` file, not in chat or source control.
2. Install the bot into your server using the application's bot installation
   link. Give it **View Channel** in the intake channel, and **View Channel**,
   **Send Messages**, **Embed Links**, and **Attach Files** in each destination.
   Check channel permission overrides too. Administrator is unnecessary.
3. Enable Developer Mode in Discord's settings. Right-click the source and
   destination channels and use **Copy Channel ID**.
4. Run these commands from this project folder (Python 3.10 or later):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   Copy-Item .env.example .env
   Copy-Item config.example.json config.json
   ```

   The copy commands are for initial setup; do not overwrite an existing config.
5. Edit `.env` to set your token. Edit `config.json` to replace the example IDs
   and addresses. Copy the full address as displayed in `Delivered To`, including
   any recipient name, unit, or ZIP code that appears in that field.
6. Start the bot:

   ```powershell
   .\.venv\Scripts\python.exe bot.py
   ```

   Wait for the `Watching channel` log line, then let a new webhook arrive.
   Keep the process and computer running. Stop with Ctrl+C.

## Address matching

Each route has one destination `channel_id` and an `addresses` list. Multiple
addresses can go to the same channel. Add alternate spellings as separate entries
in that list (for example `Street` and `St`). Matching ignores capitalization,
commas, periods, common Discord text formatting, extra spaces, and line breaks.
It keeps unit numbers and other address details; it does not use partial or fuzzy
matching. If your provider uses another field label, change `address_field`.

Unknown/missing addresses are skipped and logged by message ID, without logging
the address itself. A message with address fields for multiple destinations is
skipped rather than sending all of its cards to one recipient. Duplicate address
mappings and routing back to the intake channel are rejected at startup.

## Scope

- Handles new webhook messages received while connected. Ordinary user messages
  and ordinary bot messages are ignored.
- Does not scan old messages or catch up messages missed while offline.
- Does not synchronize edits or deletions. If your order provider edits an existing
  card instead of posting a new webhook message, edit handling needs to be added.
- Copies embed formatting, links, remote images, and attachments. Does not copy
  the webhook sender identity, interactive buttons, reactions, or thread context.
- Does not ping users, roles, or `@everyone` when copying text.
- Deduplicates the most recent 10,000 source message IDs during the current run.
  Send failures are logged; there is no persistent retry queue.
- Uses server text channels, not forum channels or threads. Restart after editing
  configuration. Run one instance per intake channel.

The screenshot is assumed to be a real Discord embed with a `Delivered To` field,
not a screenshot uploaded as an image. Confirm that field with a live webhook.

## Local checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These checks do not connect to Discord or send messages. A live test still needs
your bot token, actual channel IDs, and a new incoming webhook.

References: [discord.py message content intent](https://discordpy.readthedocs.io/en/stable/intents.html#message-content),
[Discord message objects](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx).
