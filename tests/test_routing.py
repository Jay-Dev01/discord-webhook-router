import unittest

from routing import RoutingConfig, WebhookTarget


WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/test_token"


def webhook_rule(value="08882", mode="keyword", source="1", destination="2", url=WEBHOOK):
    return {"source_channel_id": source, "channel_id": destination, "webhook_url": url,
            "mode": mode, "value": value}


def webhook_config(*rules):
    return RoutingConfig.from_dict({"source_channel_id": "1", "routes": list(rules)})


def embed(address, name="Delivered To"):
    return {"fields": [{"name": name, "value": address}]}


def config():
    return RoutingConfig.from_dict({
        "source_channel_id": "1",
        "routes": [
            {"channel_id": "2", "addresses": ["123 Main St, Apt 4", "123 Main Street, Apt 4"]},
            {"channel_id": "3", "addresses": ["123 Main St, Apt 5"]},
        ],
    })


class RoutingTests(unittest.TestCase):
    def test_channel_rules_support_both_modes(self):
        for mode, value, address in (("keyword", "08882", "123 Main St\nSouth River NJ 08882"),
                                     ("strict", "123 Main St", "123 MAIN ST.\nSouth River NJ 08882")):
            with self.subTest(mode=mode):
                rule = webhook_rule(value, mode, source="4")
                del rule["webhook_url"]
                cfg = webhook_config(rule)
                self.assertEqual(cfg.resolve([embed(address)], 4), (2, "matched"))
                self.assertIsNone(cfg.resolve([embed(address)], 1)[0])
                self.assertIsNone(cfg.resolve([embed("Unknown")], 4)[0])

    def test_empty_routes_allow_command_setup(self):
        self.assertEqual(webhook_config().resolve([embed("08882")])[1], "unmatched address")

    def test_south_river_keyword(self):
        target, reason = webhook_config(webhook_rule()).resolve([embed("123 Main St\nSouth River, NJ 08882")])
        self.assertEqual((target, reason), (WebhookTarget(WEBHOOK, 2), "matched"))

    def test_keyword_ignores_case_and_formatting(self):
        self.assertIsNotNone(webhook_config(webhook_rule("south river")).resolve([
            embed("123 Main St\n**SOUTH RIVER**, NJ 08882")])[0])

    def test_strict_first_nonempty_line(self):
        cfg = webhook_config(webhook_rule("123 Main St", "strict"))
        self.assertIsNotNone(cfg.resolve([embed("\n**123 MAIN ST.**\nSouth River, NJ 08882")])[0])
        for address in ("123 Main St Apt 4\nSouth River", "9123 Main St", "Jane Doe\n123 Main St", "123 Main Street"):
            self.assertIsNone(cfg.resolve([embed(address)])[0])

    def test_rules_are_scoped_to_source_channel(self):
        cfg = webhook_config(webhook_rule(), webhook_rule(source="4", destination="3", url=WEBHOOK + "2"))
        self.assertEqual(cfg.resolve([embed("08882")], 1)[0].channel_id, 2)
        self.assertEqual(cfg.resolve([embed("08882")], 4)[0].channel_id, 3)
        self.assertIsNone(cfg.resolve([embed("08882")], 5)[0])

    def test_overlapping_destinations_are_skipped(self):
        cfg = webhook_config(webhook_rule(), webhook_rule("123 Main St", "strict", destination="3", url=WEBHOOK + "2"))
        self.assertEqual(cfg.resolve([embed("123 Main St\n08882")])[1], "ambiguous address rules")

    def test_multiple_matching_rules_same_webhook(self):
        cfg = webhook_config(webhook_rule(), webhook_rule("123 Main St", "strict"))
        self.assertIsNotNone(cfg.resolve([embed("123 Main St\n08882")])[0])

    def test_unknown_card_blocks_keyword_forward(self):
        self.assertIsNone(webhook_config(webhook_rule()).resolve([embed("08882"), embed("10001")])[0])

    def test_invalid_rules(self):
        for rule in (webhook_rule(" "), webhook_rule(mode="fuzzy"), webhook_rule("a\nb", "strict"),
                     webhook_rule(url="https://example.com/api/webhooks/1/token"), webhook_rule(destination="1")):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                webhook_config(rule)

    def test_duplicate_rule_rejected(self):
        with self.assertRaisesRegex(ValueError, "already exists"):
            webhook_config(webhook_rule(), webhook_rule())

    def test_cross_channel_loop_rejected(self):
        with self.assertRaisesRegex(ValueError, "intake channel"):
            webhook_config(webhook_rule(), webhook_rule(source="2", destination="3"))

    def test_embed_formatting_and_wrapping(self):
        self.assertEqual(config().resolve([embed("||123 MAIN ST.\nApt 4||", "**Delivered To**")]), (2, "matched"))

    def test_explicit_alias(self):
        self.assertEqual(config().resolve([embed("123 Main Street, Apt 4")])[0], 2)

    def test_apartments_are_distinct(self):
        self.assertEqual(config().resolve([embed("123 Main St, Apt 5")])[0], 3)
        self.assertIsNone(config().resolve([embed("123 Main St, Apt 6")])[0])

    def test_partial_address_does_not_match(self):
        self.assertIsNone(config().resolve([embed("123 Main St")])[0])

    def test_missing_address_does_not_match(self):
        self.assertEqual(config().resolve([embed("123 Main St, Apt 4", "Email")])[1], "missing address field")

    def test_mixed_destinations_not_forwarded(self):
        self.assertIsNone(config().resolve([embed("123 Main St, Apt 4"), embed("123 Main St, Apt 5")])[0])

    def test_unknown_card_blocks_whole_message(self):
        self.assertIsNone(config().resolve([embed("123 Main St, Apt 4"), embed("unknown")])[0])

    def test_multiple_cards_for_same_destination(self):
        self.assertEqual(config().resolve([embed("123 Main St, Apt 4"), embed("123 Main Street, Apt 4")])[0], 2)

    def test_conflicting_mapping_rejected(self):
        with self.assertRaisesRegex(ValueError, "different channels"):
            RoutingConfig.from_dict({"source_channel_id": "1", "routes": [
                {"channel_id": "2", "addresses": ["123 Main St"]},
                {"channel_id": "3", "addresses": ["123 MAIN ST."]},
            ]})

    def test_loop_rejected(self):
        with self.assertRaisesRegex(ValueError, "intake channel"):
            RoutingConfig.from_dict({"source_channel_id": "1", "routes": [
                {"channel_id": "1", "addresses": ["123 Main St"]},
            ]})


if __name__ == "__main__":
    unittest.main()
