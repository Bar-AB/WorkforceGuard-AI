class DomainError(Exception):
    pass


class NotFoundError(DomainError):
    pass


class InvalidInputError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class UnauthenticatedError(DomainError):
    pass


class ForbiddenError(DomainError):
    pass


class ServiceUnavailableError(DomainError):
    pass
