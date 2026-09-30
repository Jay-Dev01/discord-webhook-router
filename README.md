# Discord address router

Watches Discord text channels for incoming webhook messages. Reads the
`Delivered To` embed field and forwards the content, embeds, and attachments
to a destination channel using keyword or strict first-line address rules.
Add rules with `/add` in the channel receiving the incoming messages.
Copies appear under the bot's name. Existing full-address routes still work.
The original message stays in the intake channel.

## Add a rule in Discord

Run the command in the **source channel** you want the bot to watch. You need
**Manage Channels** there, and the bot needs **View Channel** and Message Content
Intent. Enable Developer Mode in Discord's settings, right-click the destination
channel, and choose **Copy Channel ID**. Use that ID in the command:

```text
/add channel_id:222222222222222222 value:08882 mode:keyword
/add channel_id:222222222222222222 value:123 Main St mode:strict
```

- **keyword** (default): matches anywhere inside the delivery address. `08882`
  matches `123 Main St, South River, NJ 08882`. It uses substring matching.
- **strict**: matches the entire first nonempty line of the delivery address.
  `123 Main St` matches `123 Main St` followed by a city/ZIP on the next line,
  but not `123 Main St Apt 4` or `123 Main Street`. If the first line is a
  recipient name, or the whole address is on one line, that whole first line is
  what strict mode checks.

Both modes ignore capitalization, common Discord formatting, commas, periods,
and extra spaces. Only the configured address field is searched.
Rules apply only to messages in the channel where `/add` was used. They activate
immediately and save to `config.json` for restarts. Replies are private.
To remove or change a rule, edit its entry in `config.json` and restart the bot.
Restart after updating the bot so Discord receives the new `/add` options.
Previously saved webhook routes remain supported; keep their URLs private.

Overlapping rules to different destinations cause the message to be skipped;
strict rules do not override keyword rules. Messages containing addresses for
different destinations, or any unmatched address field, are also skipped.
Destination channels must belong to the same server and cannot be a watched
channel. The bot checks its destination permissions before saving a rule.
Duplicate rules are rejected.

## View routing configuration

Run `/routes` in your configured server to see every saved address rule, its
match mode, source channel, and destination channel, plus the address field
being searched. This includes rules from all intake channels and legacy
full-address or webhook routes. Webhook URLs and tokens are never included.
You need **Manage Channels** in the channel where you run the command.
The reply is private; large configurations are attached as `routes.txt` so
no addresses are cut off. Viewing routes does not change them.

## Setup (Windows / PowerShell)

1. Create an application at <https://discord.com/developers/applications>.
   On its **Bot** page, enable **Message Content Intent** and obtain a bot token.
   Keep the token in your local `.env` file, not in chat or source control.
2. Install the bot into your server using the application's bot installation
   link with the `bot` and `applications.commands` scopes. Give it **View Channel**
   in the intake and destination channels. Also give it **Send Messages**,
   **Embed Links**, and **Attach Files** in each destination.
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
5. Edit `.env` to set your token. Edit `config.json` to set `source_channel_id`
   to your initial intake channel. Leave `routes` empty to add rules in Discord.
   The bot registers `/add` in that intake channel's server at startup.
6. Start the bot:

   ```powershell
   .\.venv\Scripts\python.exe bot.py
   ```

   Wait for the `Watching channel` log line, then let a new webhook arrive.
   Keep the process and computer running. Stop with Ctrl+C.

## Legacy address matching

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

## Cloud deployment

The included `Dockerfile` runs the bot as a continuously running worker. It needs
outbound access to Discord and does not serve a website or require a public port.
Use a persistent service such as [Railway](https://docs.railway.com/build-deploy).
Vercel Functions have [execution time limits](https://vercel.com/docs/functions/limitations)
and are not suitable for running this worker unchanged.

For Railway, connect this GitHub repository as a service and use its Dockerfile.
Before starting it:

1. Attach a [persistent volume](https://docs.railway.com/volumes) at `/data`.
2. Set `DISCORD_BOT_TOKEN` as a secret environment variable.
3. Set `SOURCE_CHANNEL_ID` to the initial intake channel ID.
4. Set `ROUTER_CONFIG_PATH=/data/config.json` (also the Docker image default).
5. Run one replica, turn off sleeping/serverless mode, and enable automatic
   restarts. Do not configure an HTTP health check or a public domain.

On the first start, the bot creates an empty routing configuration on the volume.
On subsequent starts it loads the saved file, including all `/add` rules;
`SOURCE_CHANNEL_ID` only initializes a missing file. To migrate existing rules,
copy the existing `config.json` to the volume before the first start. Never put
the bot token in the Dockerfile or commit it to GitHub.

Confirm the `Watching channel` log, then use `/add` in Discord and send a new
incoming webhook to verify forwarding. Stop any local copy before starting the
hosted bot to avoid duplicate forwards. Keep the volume attached across deploys.
Hosting may require a paid plan.

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
  configuration manually. Run one instance for the configured server; `/add`
  can configure additional intake channels in that server.

The screenshot is assumed to be a real Discord embed with a `Delivered To` field,
not a screenshot uploaded as an image. Confirm that field with a live webhook.

## Local checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These checks do not connect to Discord or send messages. A live test still needs
your bot token, actual channel IDs, and a new incoming webhook.

References: [discord.py message content intent](https://discordpy.readthedocs.io/en/stable/intents.html#message-content),
[Discord message objects](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx),
[discord.py slash commands](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.app_commands.CommandTree).
