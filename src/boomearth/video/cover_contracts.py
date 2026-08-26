"""Stable cover contract identifiers shared across video production stages."""

LEGACY_PLATFORM_COVER_CONTRACT = "platform-defaults-v1"
DEFAULT_PUNK_COVER_CONTRACT = "punk-cover-giant-title-3x4-v1"
SUPPORTED_COVER_CONTRACTS = frozenset(
    {LEGACY_PLATFORM_COVER_CONTRACT, DEFAULT_PUNK_COVER_CONTRACT}
)


__all__ = (
    "DEFAULT_PUNK_COVER_CONTRACT",
    "LEGACY_PLATFORM_COVER_CONTRACT",
    "SUPPORTED_COVER_CONTRACTS",
)
