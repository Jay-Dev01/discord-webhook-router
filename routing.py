"""Address matching without Discord or network dependencies."""

import re
import unicodedata
from dataclasses import dataclass


def normalize(value: str) -> str:
    # Ignore common embed formatting, casing, punctuation and line wrapping.
    # Keep unit numbers, # signs, hyphens and all other address information.
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[*_`|]", "", value)
    value = value.replace(",", " ").replace(".", "")
    return " ".join(value.split())


def channel_id(value: object) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(str(value)) <= 0:
        raise ValueError("Channel IDs must be positive numbers copied from Discord.")
    return int(str(value))


@dataclass
class RoutingConfig:
    source_channel_id: int
    address_field: str
    destinations: dict[str, int]

    @classmethod
    def from_dict(cls, data: dict) -> "RoutingConfig":
        source = channel_id(data["source_channel_id"])
        field = data.get("address_field", "Delivered To")
        if not isinstance(field, str) or not normalize(field):
            raise ValueError("address_field must be a nonempty string.")
        destinations: dict[str, int] = {}
        if not isinstance(data["routes"], list):
            raise ValueError("routes must be a list.")
        for route in data["routes"]:
            destination = channel_id(route["channel_id"])
            if source == destination:
                raise ValueError("A destination cannot be the intake channel.")
            addresses = route["addresses"]
            if not isinstance(addresses, list) or not addresses:
                raise ValueError("Each route needs a nonempty addresses list.")
            for address in addresses:
                if not isinstance(address, str) or not normalize(address):
                    raise ValueError("Addresses must be nonempty strings.")
                key = normalize(address)
                if key in destinations and destinations[key] != destination:
                    raise ValueError("An address is assigned to different channels.")
                destinations[key] = destination
        if not destinations:
            raise ValueError("Configure at least one address route.")
        return cls(source, normalize(field.rstrip(":")), destinations)

    def resolve(self, embeds: list[dict]) -> tuple[int | None, str]:
        addresses = {
            normalize(field.get("value", ""))
            for embed in embeds
            for field in embed.get("fields", [])
            if normalize(field.get("name", "").rstrip(":")) == self.address_field
        }
        if not addresses:
            return None, "missing address field"
        # Never forward an entire message if one of its cards belongs elsewhere
        # or contains an unknown address.
        if any(address not in self.destinations for address in addresses):
            return None, "unmatched address"
        channels = {self.destinations[address] for address in addresses}
        if len(channels) != 1:
            return None, "multiple destination channels in one message"
        return channels.pop(), "matched"
