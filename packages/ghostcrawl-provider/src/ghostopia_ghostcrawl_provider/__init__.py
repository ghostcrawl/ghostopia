"""ghostopia-ghostcrawl-provider — the ONLY package that imports the ghostcrawl SDK.

Implements the shared ``BrowserProvider`` Protocol over the real ghostcrawl Python SDK
(``GhostCrawlProvider``), owns the dual-target registry (``TargetRegistry``), and
maps the SDK error catalog to normalized ghostopia events (``map_sdk_error``). The
full-primitive surface is wired to the SDK; the raw HELD mouse drag rides the ONE
documented non-SDK path — the :class:`CdpWsTransport` over the ``cdp.url()`` relay, with
ergonomic ``make_input_helpers`` mouse/keyboard on top (chromium relay + cdp.input degrade).
"""

from ghostopia_ghostcrawl_provider.cdp_transport import (
    CdpWsTransport,
    create_cdp_ws_transport,
)
from ghostopia_ghostcrawl_provider.error_map import MappedError, map_sdk_error
from ghostopia_ghostcrawl_provider.input_helpers import (
    FeatureUnavailable,
    make_input_helpers,
)
from ghostopia_ghostcrawl_provider.provider import GhostCrawlProvider, ProviderCallError
from ghostopia_ghostcrawl_provider.target_registry import (
    TargetRegistry,
    UnknownTargetError,
)

__all__ = [
    "GhostCrawlProvider",
    "ProviderCallError",
    "TargetRegistry",
    "UnknownTargetError",
    "MappedError",
    "map_sdk_error",
    "CdpWsTransport",
    "create_cdp_ws_transport",
    "make_input_helpers",
    "FeatureUnavailable",
]
