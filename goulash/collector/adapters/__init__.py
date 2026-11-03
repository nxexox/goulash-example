from goulash.collector.adapters.base import CatalogRecord, ListingBatch, SourceAdapter
from goulash.collector.adapters.prodexpo import ProdexpoAdapter
from goulash.collector.adapters.worldfood import WorldFoodAdapter

# SPPI (sppiunion.ru) is deliberately absent: its pages say copying is allowed only with the
# administration's permission. Add an adapter only after terms are agreed (see PoC/PLAN.md §3).
ADAPTERS: dict[str, SourceAdapter] = {a.id: a for a in (ProdexpoAdapter(), WorldFoodAdapter())}

__all__ = ["ADAPTERS", "CatalogRecord", "ListingBatch", "SourceAdapter"]
