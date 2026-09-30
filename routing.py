"""Address matching without Discord or network dependencies."""

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field


def webhook_url(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"https://(?:www\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/[0-9]+/[A-Za-z0-9_-]+",
        value.strip(),
    ):
        raise ValueError("Enter a valid Discord webhook URL.")
    return value.strip()


@dataclass(frozen=True)
class WebhookTarget:
    url: str = field(repr=False)
    channel_id: int


@dataclass(frozen=True)
class Rule:
    source: int
    mode: str
    value: str
    target: int | WebhookTarget

    def matches(self, address: str) -> bool:
        if self.mode == "strict":
            first = next((line for line in address.splitlines() if normalize(line)), "")
            return normalize(first) == self.value
        if self.mode == "keyword":
            return self.value in normalize(address)
        return normalize(address) == self.value


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
    rules: list[Rule]
    data: dict = field(repr=False)

    @property
    def address_field_names(self) -> tuple[str, ...]:
        names = ("Delivered To", "Shipping Address")
        if self.address_field not in {normalize(name) for name in names}:
            names += (self.data.get("address_field", self.address_field),)
        return names

    @property
    def source_ids(self) -> set[int]:
        return {self.source_channel_id} | {rule.source for rule in self.rules}

    @classmethod
    def from_dict(cls, data: dict) -> "RoutingConfig":
        source = channel_id(data["source_channel_id"])
        field = data.get("address_field", "Delivered To")
        if not isinstance(field, str) or not normalize(field):
            raise ValueError("address_field must be a nonempty string.")
        destinations: dict[str, int] = {}
        rules: list[Rule] = []
        if not isinstance(data["routes"], list):
            raise ValueError("routes must be a list.")
        for route in data["routes"]:
            destination = channel_id(route["channel_id"])
            route_source = channel_id(route.get("source_channel_id", source))
            if route_source == destination:
                raise ValueError("A destination cannot be the intake channel.")
            if "webhook_url" in route or "mode" in route or "value" in route:
                mode = route.get("mode", "keyword")
                if mode not in ("keyword", "strict"):
                    raise ValueError("Mode must be keyword or strict.")
                value = route.get("value")
                if not isinstance(value, str) or not normalize(value):
                    raise ValueError("Enter a nonempty keyword or line 1 address.")
                if mode == "strict" and len(value.strip().splitlines()) != 1:
                    raise ValueError("Strict mode accepts only the first address line.")
                target = (WebhookTarget(webhook_url(route["webhook_url"]), destination)
                          if "webhook_url" in route else destination)
                rule = Rule(route_source, mode, normalize(value), target)
                for existing in rules:
                    if (existing.source, existing.mode, existing.value) == (rule.source, mode, rule.value):
                        if existing.target != target:
                            raise ValueError("This match is already assigned to another destination.")
                        raise ValueError("This rule already exists.")
                rules.append(rule)
                continue
            if route_source != source:
                raise ValueError("Legacy address routes must use the configured intake channel.")
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
        rules.extend(Rule(source, "full", key, target) for key, target in destinations.items())
        sources = {source} | {rule.source for rule in rules}
        if any((rule.target.channel_id if isinstance(rule.target, WebhookTarget) else rule.target)
               in sources for rule in rules):
            raise ValueError("A destination cannot be an intake channel.")
        return cls(source, normalize(field.rstrip(":")), destinations, rules, deepcopy(data))

    def resolve(self, embeds: list[dict], source_id: int | None = None) -> tuple[int | WebhookTarget | None, str]:
        source_id = self.source_channel_id if source_id is None else source_id
        address_fields = {normalize(name.rstrip(":")) for name in self.address_field_names}
        addresses = {
            field.get("value", "")
            for embed in embeds
            for field in embed.get("fields", [])
            if normalize(field.get("name", "").rstrip(":")) in address_fields
        }
        if not addresses:
            return None, "missing address field"
        # Never forward an entire message if one of its cards belongs elsewhere
        # or contains an unknown address.
        channels = set()
        for address in addresses:
            targets = {rule.target for rule in self.rules
                       if rule.source == source_id and rule.matches(address)}
            if not targets:
                return None, "unmatched address"
            if len(targets) != 1:
                return None, "ambiguous address rules"
            channels.update(targets)
        if len(channels) != 1:
            return None, "multiple destination channels in one message"
        return channels.pop(), "matched"
