class OrganizationMismatchError(ValueError):
    pass


class EventIdConflictError(ValueError):
    pass


class StorageUnavailableError(Exception):
    pass


class IntegrationError(Exception):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def is_permanent_error(error: Exception) -> bool:
    return isinstance(error, ValueError) or (
        isinstance(error, IntegrationError) and error.permanent
    )
