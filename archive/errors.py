"""Shared failure types, independent of the HTTP client and API adapters."""


class ArchiveError(Exception):
    pass


class LoginRequired(ArchiveError):
    pass


class Throttled(ArchiveError):
    pass
