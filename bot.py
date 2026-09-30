"""Copy incoming webhook messages to channels selected by delivery address."""

import asyncio
import io
import json
import logging
import os
from collections import deque
from copy import deepcopy
from pathlib import Path
from typing import Literal

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

from config_store import load_config, save_config
from routing import RoutingConfig, WebhookTarget, channel_id as parse_channel_id, normalize

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("address_router")


class AddressRouter(discord.Client):
    def __init__(self, config: RoutingConfig, config_path: Path = BASE / "config.json"):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.config_path = config_path
        self.webhook_session: aiohttp.ClientSession | None = None
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(app_commands.Command(
            name="add", description="Forward this channel's matching deliveries to a destination channel.",
            callback=self.add_route,
        ))
        self.tree.add_command(app_commands.Command(
            name="routes", description="View all address rules and their source and destination channels.",
            callback=self.list_routes,
        ))
        self.tree.add_command(app_commands.Command(
            name="delete", description="Remove an address rule from this source channel.",
            callback=self.delete_route,
        ))
        self.routing_ready = False
        self.lock = asyncio.Lock()
        self.recent_ids: deque[int] = deque(maxlen=10000)
        self.seen: set[int] = set()

    async def setup_hook(self):
        self.webhook_session = aiohttp.ClientSession()
        source = await self.get_text_channel(self.config.source_channel_id)
        self.tree.copy_global_to(guild=source.guild)
        await self.tree.sync(guild=source.guild)

    async def close(self):
        if self.webhook_session is not None:
            await self.webhook_session.close()
        await super().close()

    def make_webhook(self, url: str) -> discord.Webhook:
        return discord.Webhook.from_url(url, session=self.webhook_session)

    def save_config(self, config: RoutingConfig):
        save_config(self.config_path, config)

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(channel_id="Destination channel ID (right-click the channel and Copy Channel ID)",
                           value="Keyword (e.g. 08882) or exact line 1 address",
                           mode="keyword searches the address; strict matches its first nonempty line")
    async def add_route(self, interaction: discord.Interaction, channel_id: str, value: str,
                        mode: Literal["keyword", "strict"] = "keyword"):
        if not interaction.permissions.manage_channels:
            await interaction.response.send_message("You need Manage Channels to add routes.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if not self.routing_ready:
                raise ValueError("The router is still starting. Try again shortly.")
            source = await self.get_text_channel(interaction.channel_id)
            intake = await self.get_text_channel(self.config.source_channel_id)
            if source.guild.id != intake.guild.id or interaction.guild_id != intake.guild.id:
                raise ValueError("Use this command in the configured intake server.")
            if source.guild.me is None or not source.permissions_for(source.guild.me).view_channel:
                raise ValueError("The bot needs View Channel in this source channel.")
            destination = await self.get_text_channel(parse_channel_id(channel_id.strip()))
            self.validate_destination(destination, source.guild)
            async with self.lock:
                data = deepcopy(self.config.data)
                data["routes"].append({"source_channel_id": str(source.id),
                                       "channel_id": str(destination.id),
                                       "mode": mode, "value": value})
                updated = RoutingConfig.from_dict(data)
                self.save_config(updated)
                self.config = updated
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("Could not access the channel. Check the channel ID and bot permissions.", ephemeral=True)
            return
        except (OSError, aiohttp.ClientError):
            await interaction.followup.send("Could not save or verify the rule. No rule was added; try again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Saved {mode} rule for <#{source.id}> → <#{destination.id}>. It is active now.",
            ephemeral=True,
        )

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(channel_id="Destination channel ID of the rule to remove",
                           value="Existing keyword or address shown by /routes",
                           mode="Match mode of the rule; full is for legacy full-address rules")
    async def delete_route(self, interaction: discord.Interaction, channel_id: str, value: str,
                           mode: Literal["keyword", "strict", "full"] = "keyword"):
        if not interaction.permissions.manage_channels:
            await interaction.response.send_message("You need Manage Channels to delete routes.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if not self.routing_ready:
                raise ValueError("The router is still starting. Try again shortly.")
            intake = await self.get_text_channel(self.config.source_channel_id)
            if interaction.guild_id != intake.guild.id:
                raise ValueError("Use this command in the configured intake server.")
            destination = parse_channel_id(channel_id.strip())
            match = normalize(value)
            if not match:
                raise ValueError("Enter the existing keyword or address shown by /routes.")
            async with self.lock:
                data = deepcopy(self.config.data)
                remaining = []
                removed = False
                for route in data["routes"]:
                    source = int(route.get("source_channel_id", self.config.source_channel_id))
                    route_mode = route.get("mode", "keyword") if "value" in route else "full"
                    if (source != interaction.channel_id or int(route["channel_id"]) != destination
                            or route_mode != mode):
                        remaining.append(route)
                    elif "value" in route:
                        if normalize(route["value"]) == match:
                            removed = True
                        else:
                            remaining.append(route)
                    else:
                        addresses = [address for address in route["addresses"] if normalize(address) != match]
                        removed |= len(addresses) != len(route["addresses"])
                        if addresses:
                            route["addresses"] = addresses
                            remaining.append(route)
                if not removed:
                    raise ValueError("No matching rule in this source channel. Check /routes for its destination, value, and mode.")
                data["routes"] = remaining
                updated = RoutingConfig.from_dict(data)
                self.save_config(updated)
                self.config = updated
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("Could not verify the intake server. No rule was deleted.", ephemeral=True)
            return
        except (OSError, aiohttp.ClientError):
            await interaction.followup.send("Could not save or verify the change. No rule was deleted; try again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Deleted {mode} rule for <#{interaction.channel_id}> → <#{destination}>. The change is active now.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def list_routes(self, interaction: discord.Interaction):
        if not interaction.permissions.manage_channels:
            await interaction.response.send_message("You need Manage Channels to view routes.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not self.routing_ready:
            await interaction.followup.send("The router is still starting. Try again shortly.", ephemeral=True)
            return
        try:
            intake = await self.get_text_channel(self.config.source_channel_id)
        except (discord.HTTPException, ValueError, aiohttp.ClientError):
            await interaction.followup.send("Could not verify the intake server. Try again shortly.", ephemeral=True)
            return
        if interaction.guild_id != intake.guild.id:
            await interaction.followup.send("Use this command in the configured intake server.", ephemeral=True)
            return

        # Only export routing fields, never the saved webhook credentials.
        config = self.config
        lines = ["Routing configuration",
                 f"Initial intake: <#{config.source_channel_id}>",
                 "Address field: " + json.dumps(config.data.get("address_field", "Delivered To"), ensure_ascii=False)]
        count = 0
        for route in config.data["routes"]:
            source = int(route.get("source_channel_id", config.source_channel_id))
            destination = int(route["channel_id"])
            mode = route.get("mode", "keyword") if "value" in route else "full address"
            values = [route["value"]] if "value" in route else route["addresses"]
            delivery = " (legacy webhook)" if "webhook_url" in route else ""
            for value in values:
                count += 1
                lines.append(f"\n{count}. <#{source}> → <#{destination}>{delivery}\n"
                             f"Match ({mode}): {json.dumps(value, ensure_ascii=False)}")
        lines.insert(1, f"{count} configured address rules")
        if not count:
            lines.append("\nNo routes configured yet. Use /add in a source channel to create one.")
        report = "\n".join(lines)
        content = discord.utils.escape_markdown(report)
        # Discord limits message content to 2,000 characters. Keep the full
        # report available without truncating addresses or sending many replies.
        if len(content.encode("utf-16-le")) // 2 <= 2000:
            await interaction.followup.send(content, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)
        else:
            with io.BytesIO(report.encode("utf-8")) as buffer:
                file = discord.File(buffer, filename="routes.txt")
                try:
                    await interaction.followup.send(
                        f"{count} configured address rules. The complete list is attached (channel IDs appear as <#ID>).",
                        file=file, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                    )
                finally:
                    file.close()

    async def get_text_channel(self, ident: int) -> discord.TextChannel:
        channel = self.get_channel(ident) or await self.fetch_channel(ident)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError(f"Channel {ident} must be a server text channel.")
        return channel

    def validate_destination(self, destination: discord.TextChannel, guild: discord.Guild):
        if destination.guild.id != guild.id:
            raise ValueError("All destinations must be in the intake server.")
        member = guild.me
        if member is None:
            raise ValueError("Cannot check the bot's permissions in this server.")
        permissions = destination.permissions_for(member)
        required = ("view_channel", "send_messages", "embed_links", "attach_files")
        missing = [name for name in required if not getattr(permissions, name)]
        if missing:
            raise ValueError(f"Channel {destination.id} is missing permissions: {', '.join(missing)}")

    async def on_ready(self):
        self.routing_ready = False
        try:
            source = await self.get_text_channel(self.config.source_channel_id)
            member = source.guild.me
            if member is None or not source.permissions_for(member).view_channel:
                raise ValueError("The bot needs View Channel in the intake channel.")
            for ident in self.config.source_ids:
                intake = await self.get_text_channel(ident)
                if intake.guild.id != source.guild.id or not intake.permissions_for(member).view_channel:
                    raise ValueError("All intake channels must be visible to the bot in the same server.")
            for ident in {rule.target for rule in self.config.rules if isinstance(rule.target, int)}:
                destination = await self.get_text_channel(ident)
                self.validate_destination(destination, source.guild)
        except (discord.HTTPException, ValueError) as error:
            LOG.error("Startup validation failed: %s", error)
            await self.close()
            return
        self.routing_ready = True
        LOG.info("Connected as %s. Watching channel %s; %s address entries configured.",
                 self.user, source.id, len(self.config.rules))

    async def on_message(self, message: discord.Message):
        # Webhook authors are also bots, so do not filter on author.bot.
        if (message.channel.id not in self.config.source_ids
                or message.guild is None or message.webhook_id is None):
            return
        # Ignore our outgoing webhooks even if someone moves one to an intake.
        if any(isinstance(rule.target, WebhookTarget)
               and int(rule.target.url.split("/")[-2]) == message.webhook_id for rule in self.config.rules):
            return
        await self.wait_until_ready()
        if not self.routing_ready:
            return
        async with self.lock:
            if message.id in self.seen:
                return
            target, reason = self.config.resolve([embed.to_dict() for embed in message.embeds], message.channel.id)
            if target is None:
                # IDs and reasons only: avoid putting addresses/order data in logs.
                LOG.warning("Skipped message %s: %s", message.id, reason)
                return
            files: list[discord.File] = []
            try:
                is_webhook = isinstance(target, WebhookTarget)
                destination_id = target.channel_id if is_webhook else target
                if is_webhook:
                    destination = await self.make_webhook(target.url).fetch()
                    guild_id = destination.guild_id
                    if destination.channel_id != destination_id or destination.channel_id in self.config.source_ids:
                        LOG.error("Blocked message %s: webhook destination changed", message.id)
                        return
                else:
                    destination = await self.get_text_channel(destination_id)
                    guild_id = destination.guild.id
                if guild_id != message.guild.id:
                    LOG.error("Blocked message %s: destination is in another server", message.id)
                    return
                for attachment in message.attachments:
                    files.append(await attachment.to_file())
                await destination.send(
                    content=message.content or None,
                    embeds=[embed.copy() for embed in message.embeds],
                    files=files,
                    allowed_mentions=discord.AllowedMentions.none(),
                    **({"wait": True} if is_webhook else {}),
                )
            except discord.HTTPException as error:
                LOG.error("Failed to forward message %s (Discord HTTP %s, code %s)",
                          message.id, error.status, error.code)
                return
            except (aiohttp.ClientError, OSError, ValueError):
                LOG.error("Failed to forward message %s: network or destination error", message.id)
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
        raise SystemExit("Set DISCORD_BOT_TOKEN in the host environment or your local .env file.")
    config_path = Path(os.getenv("ROUTER_CONFIG_PATH", "").strip() or BASE / "config.json")
    try:
        config = load_config(config_path, os.getenv("SOURCE_CHANNEL_ID", ""))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise SystemExit(f"Check routing configuration at {config_path}: {error}") from error
    client = AddressRouter(config, config_path)
    try:
        client.run(token, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the bot token. Check DISCORD_BOT_TOKEN in .env.")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Enable Message Content Intent on the Bot page in the Discord Developer Portal.")


if __name__ == "__main__":
    main()
