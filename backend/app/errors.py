"""Domain errors. Services raise these; only the API layer maps them to HTTP."""


class DomainError(Exception):
    pass


class NotFoundError(DomainError):
    """The thing does not exist, or belongs to another company."""


class InvalidInputError(DomainError):
    """The caller sent something the domain cannot use."""


class ConflictError(DomainError):
    """The request is fine, but the current data does not allow it."""
