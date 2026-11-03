from collections.abc import Iterator
from dataclasses import asdict, dataclass, field

from goulash.collector.html import Page


@dataclass
class CatalogRecord:
    """One company as described by one source page. Not yet a verified supplier."""

    source: str
    edition: str
    external_id: str
    source_url: str
    name: str
    website: str | None = None
    city: str | None = None
    country: str | None = None
    address: str | None = None
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    description: str = ""
    rubrics: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ListingBatch:
    card_urls: list[str]
    checkpoint: dict  # what to persist so the next run resumes after this batch
    done: bool = False


class SourceAdapter:
    """A catalog source. Adapters only understand page structure; fetching/politeness live in the fetcher."""

    id: str
    title: str
    edition: str
    entry_url: str
    enabled_by_default: bool = True
    terms_note: str | None = None

    def listing(self, get_page, checkpoint: dict | None, max_pages: int) -> Iterator[ListingBatch]:
        """Walk listing pages following real pagination links. `get_page(url) -> Page`."""
        raise NotImplementedError

    def external_id(self, url: str) -> str:
        raise NotImplementedError

    def parse_card(self, page: Page) -> CatalogRecord | None:
        raise NotImplementedError


def field_after(lines: list[str], label: str) -> str | None:
    """Value on the line following 'Label:' (catalog cards render label and value as separate blocks)."""
    for i, line in enumerate(lines[:-1]):
        if line.rstrip(":").strip().lower() == label.lower() and line.endswith(":"):
            return lines[i + 1].strip() or None
        if line.lower().startswith(label.lower() + ":") and len(line) > len(label) + 1:
            return line.split(":", 1)[1].strip() or None
    return None
