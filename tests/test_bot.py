import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from bot import AddressRouter
from routing import RoutingConfig
from test_routing import config, webhook_config, webhook_rule, WEBHOOK


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = AddressRouter(config())
        self.bot.routing_ready = True
        self.bot.wait_until_ready = AsyncMock()
        self.destination = SimpleNamespace(guild=SimpleNamespace(id=10), send=AsyncMock())
        self.bot.get_text_channel = AsyncMock(return_value=self.destination)
        card = discord.Embed(title="Delivered from Target")
        card.add_field(name="Delivered To", value="123 Main St, Apt 4")
        self.message = SimpleNamespace(
            id=100, channel=SimpleNamespace(id=1), guild=SimpleNamespace(id=10),
            webhook_id=999, content="@everyone", embeds=[card], attachments=[],
        )

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_copy_preserves_embed_and_disables_mentions(self):
        await self.bot.on_message(self.message)
        self.bot.get_text_channel.assert_awaited_once_with(2)
        sent = self.destination.send.call_args.kwargs
        self.assertEqual(sent["embeds"][0].to_dict(), self.message.embeds[0].to_dict())
        self.assertEqual(sent["content"], "@everyone")
        self.assertEqual(sent["allowed_mentions"].to_dict()["parse"], [])

    async def test_replayed_event_only_sent_once(self):
        await self.bot.on_message(self.message)
        await self.bot.on_message(self.message)
        self.destination.send.assert_awaited_once()

    async def test_normal_message_ignored(self):
        self.message.webhook_id = None
        await self.bot.on_message(self.message)
        self.destination.send.assert_not_awaited()

    async def test_other_channel_ignored(self):
        self.message.channel.id = 500
        await self.bot.on_message(self.message)
        self.destination.send.assert_not_awaited()

    async def test_other_server_blocked(self):
        self.destination.guild.id = 20
        with self.assertLogs("address_router", level="ERROR"):
            await self.bot.on_message(self.message)
        self.destination.send.assert_not_awaited()

    async def test_failed_send_is_not_recorded_as_success(self):
        self.destination.send.side_effect = discord.HTTPException(
            SimpleNamespace(status=403, reason="Forbidden"), {"code": 50013, "message": "Missing Permissions"}
        )
        with self.assertLogs("address_router", level="ERROR"):
            await self.bot.on_message(self.message)
        self.assertNotIn(self.message.id, self.bot.seen)
        self.destination.send.side_effect = None
        await self.bot.on_message(self.message)
        self.assertIn(self.message.id, self.bot.seen)

    def prepare_webhook(self):
        self.bot.config = webhook_config(webhook_rule())
        self.message.embeds[0].set_field_at(0, name="Delivered To", value="123 Main St\nSouth River NJ 08882")
        self.hook = SimpleNamespace(guild_id=10, channel_id=2, type=discord.WebhookType.incoming, send=AsyncMock())
        self.bot.make_webhook = Mock(return_value=SimpleNamespace(fetch=AsyncMock(return_value=self.hook)))

    async def test_webhook_send_and_attachment_cleanup(self):
        self.prepare_webhook()
        file = Mock()
        self.message.attachments = [SimpleNamespace(to_file=AsyncMock(return_value=file))]
        await self.bot.on_message(self.message)
        sent = self.hook.send.call_args.kwargs
        self.assertTrue(sent["wait"])
        self.assertEqual(sent["files"], [file])
        self.assertEqual(sent["allowed_mentions"].to_dict()["parse"], [])
        file.close.assert_called_once()
        self.assertIn(self.message.id, self.bot.seen)

    async def test_moved_webhook_is_blocked(self):
        self.prepare_webhook()
        self.hook.channel_id = 1
        with self.assertLogs("address_router", level="ERROR"):
            await self.bot.on_message(self.message)
        self.hook.send.assert_not_awaited()

    async def test_own_webhook_is_ignored(self):
        self.prepare_webhook()
        self.message.webhook_id = 123456789012345678
        await self.bot.on_message(self.message)
        self.bot.make_webhook.assert_not_called()

    async def test_webhook_failure_can_be_retried(self):
        self.prepare_webhook()
        self.hook.send.side_effect = discord.HTTPException(SimpleNamespace(status=404, reason="Not Found"), "Missing")
        with self.assertLogs("address_router", level="ERROR"):
            await self.bot.on_message(self.message)
        self.assertNotIn(self.message.id, self.bot.seen)
        self.hook.send.side_effect = None
        await self.bot.on_message(self.message)
        self.assertIn(self.message.id, self.bot.seen)

    def prepare_command(self):
        self.bot.config = webhook_config()
        self.source = SimpleNamespace(id=4, guild=SimpleNamespace(id=10, me=object()),
                                      permissions_for=lambda member: SimpleNamespace(view_channel=True))
        self.destination.id = 222222222222222222
        self.destination.permissions_for = Mock(return_value=SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True, attach_files=True))
        self.bot.get_text_channel = AsyncMock(side_effect=lambda ident:
            self.destination if ident == self.destination.id else self.source)
        return SimpleNamespace(channel_id=4, guild_id=10, permissions=SimpleNamespace(manage_channels=True),
                               response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()))

    async def test_command_saves_rule_and_restores_after_restart(self):
        interaction = self.prepare_command()
        with tempfile.TemporaryDirectory() as folder:
            self.bot.config_path = Path(folder) / "config.json"
            await self.bot.add_route(interaction, str(self.destination.id), "08882")
            restored = RoutingConfig.from_dict(json.loads(self.bot.config_path.read_text()))
        self.assertEqual(restored.resolve([{"fields": [{"name": "Delivered To", "value": "08882"}]}], 4)[0], self.destination.id)
        self.assertNotIn("webhook_url", restored.data["routes"][0])
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertNotIn(WEBHOOK, interaction.followup.send.call_args.args[0])
        self.assertEqual(len(self.bot.config.rules), 1)
        self.message.channel.id = 4
        self.message.embeds[0].set_field_at(0, name="Delivered To", value="South River NJ 08882")
        await self.bot.on_message(self.message)
        self.destination.send.assert_awaited_once()

    async def test_failed_save_does_not_activate_rule(self):
        interaction = self.prepare_command()
        self.bot.save_config = Mock(side_effect=OSError("disk full"))
        await self.bot.add_route(interaction, str(self.destination.id), "08882")
        self.assertEqual(self.bot.config.rules, [])
        self.assertIn("No rule was added", interaction.followup.send.call_args.args[0])

    async def test_command_requires_manage_channels(self):
        interaction = self.prepare_command()
        interaction.permissions.manage_channels = False
        await self.bot.add_route(interaction, str(self.destination.id), "08882")
        self.bot.get_text_channel.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_command_rejects_other_server_channel(self):
        interaction = self.prepare_command()
        self.destination.guild.id = 20
        self.bot.save_config = Mock()
        await self.bot.add_route(interaction, str(self.destination.id), "08882")
        self.bot.save_config.assert_not_called()
        self.assertIn("intake server", interaction.followup.send.call_args.args[0])

    async def test_command_rejects_missing_destination_permissions(self):
        for permission in ("view_channel", "send_messages", "embed_links", "attach_files"):
            with self.subTest(permission=permission):
                interaction = self.prepare_command()
                setattr(self.destination.permissions_for.return_value, permission, False)
                self.bot.save_config = Mock()
                await self.bot.add_route(interaction, str(self.destination.id), "08882")
                self.bot.save_config.assert_not_called()
                self.assertIn(permission, interaction.followup.send.call_args.args[0])

    async def test_command_rejects_invalid_channel_ids(self):
        for ident in ("not-a-channel", "0", "-1", WEBHOOK):
            with self.subTest(ident=ident):
                interaction = self.prepare_command()
                self.bot.save_config = Mock()
                await self.bot.add_route(interaction, ident, "08882")
                self.bot.save_config.assert_not_called()
                self.assertIn("Channel IDs", interaction.followup.send.call_args.args[0])

    async def test_command_rejects_source_as_destination(self):
        interaction = self.prepare_command()
        self.source.permissions_for = self.destination.permissions_for
        self.bot.save_config = Mock()
        await self.bot.add_route(interaction, "4", "08882")
        self.bot.save_config.assert_not_called()
        self.assertIn("intake channel", interaction.followup.send.call_args.args[0])

    async def test_command_schema(self):
        command = self.bot.tree.get_command("add")
        self.assertEqual([p.name for p in command.parameters], ["channel_id", "value", "mode"])
        self.assertEqual(command.parameters[0].type, discord.AppCommandOptionType.string)
        self.assertEqual([c.value for c in command.parameters[2].choices], ["keyword", "strict"])
        self.assertTrue(command.default_permissions.manage_channels)


if __name__ == "__main__":
    unittest.main()
