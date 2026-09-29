import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from bot import AddressRouter
from test_routing import config


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


if __name__ == "__main__":
    unittest.main()
