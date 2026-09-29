"""Copy incoming webhook messages to channels selected by delivery address."""

import asyncio
import json
import logging
import os
from collections import deque
from pathlib import Path

import discord
from dotenv import load_dotenv

from routing import RoutingConfig

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("address_router")


class AddressRouter(discord.Client):
    def __init__(self, config: RoutingConfig):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.routing_ready = False
        self.lock = asyncio.Lock()
        self.recent_ids: deque[int] = deque(maxlen=10000)
        self.seen: set[int] = set()

    async def get_text_channel(self, ident: int) -> discord.TextChannel:
        channel = self.get_channel(ident) or await self.fetch_channel(ident)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError(f"Channel {ident} must be a server text channel.")
        return channel

    async def on_ready(self):
        self.routing_ready = False
        try:
            source = await self.get_text_channel(self.config.source_channel_id)
            member = source.guild.me
            if member is None or not source.permissions_for(member).view_channel:
                raise ValueError("The bot needs View Channel in the intake channel.")
            for ident in set(self.config.destinations.values()):
                destination = await self.get_text_channel(ident)
                if destination.guild.id != source.guild.id:
                    raise ValueError("All destinations must be in the intake server.")
                permissions = destination.permissions_for(member)
                required = ("view_channel", "send_messages", "embed_links", "attach_files")
                missing = [name for name in required if not getattr(permissions, name)]
                if missing:
                    raise ValueError(f"Channel {ident} is missing permissions: {', '.join(missing)}")
        except (discord.HTTPException, ValueError) as error:
            LOG.error("Startup validation failed: %s", error)
            await self.close()
            return
        self.routing_ready = True
        LOG.info("Connected as %s. Watching channel %s; %s address entries configured.",
                 self.user, source.id, len(self.config.destinations))

    async def on_message(self, message: discord.Message):
        # Webhook authors are also bots, so do not filter on author.bot.
        if (message.channel.id != self.config.source_channel_id
                or message.guild is None or message.webhook_id is None):
            return
        await self.wait_until_ready()
        if not self.routing_ready:
            return
        async with self.lock:
            if message.id in self.seen:
                return
            destination_id, reason = self.config.resolve([embed.to_dict() for embed in message.embeds])
            if destination_id is None:
                # IDs and reasons only: avoid putting addresses/order data in logs.
                LOG.warning("Skipped message %s: %s", message.id, reason)
                return
            files: list[discord.File] = []
            try:
                destination = await self.get_text_channel(destination_id)
                if destination.guild.id != message.guild.id:
                    LOG.error("Blocked message %s: destination is in another server", message.id)
                    return
                for attachment in message.attachments:
                    files.append(await attachment.to_file())
                await destination.send(
                    content=message.content or None,
                    embeds=[embed.copy() for embed in message.embeds],
                    files=files,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException as error:
                LOG.error("Failed to forward message %s (Discord HTTP %s, code %s)",
                          message.id, error.status, error.code)
                return
            finally:
                for file in files:
                    file.close()
            if len(self.recent_ids) == self.recent_ids.maxlen:
                self.seen.discard(self.recent_ids[0])
            self.recent_ids.append(message.id)
            self.seen.add(message.id)
            LOG.info("Forwarded message %s to channel %s", message.id, destination_id)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv(BASE / ".env")
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token or token == "put_your_bot_token_here":
        raise SystemExit("Copy .env.example to .env and set DISCORD_BOT_TOKEN locally.")
    try:
        config = RoutingConfig.from_dict(json.loads((BASE / "config.json").read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise SystemExit(f"Check config.json (copy config.example.json to get started): {error}") from error
    client = AddressRouter(config)
    try:
        client.run(token, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the bot token. Check DISCORD_BOT_TOKEN in .env.")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Enable Message Content Intent on the Bot page in the Discord Developer Portal.")


if __name__ == "__main__":
    main()
