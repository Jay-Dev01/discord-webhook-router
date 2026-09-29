import unittest

from routing import RoutingConfig


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
