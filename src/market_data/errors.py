class MarketDataError(Exception):
    """Base error safe for display."""


class ConfigError(MarketDataError):
    pass


class SchemaError(MarketDataError):
    pass


class LockActive(MarketDataError):
    pass
