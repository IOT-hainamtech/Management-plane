class OrganizationMismatchError(ValueError):
    """An inbound event or Frappe document belongs to another organization."""


class EventIdConflictError(ValueError):
    """An event_id was already persisted with a different payload."""


class StorageUnavailableError(Exception):
    """The durable event inbox could not commit or read its ACK."""


class IntegrationError(Exception):
    """An external system failed; permanence is decided by its adapter."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def is_permanent_error(error: Exception) -> bool:
    return isinstance(error, ValueError) or (
        isinstance(error, IntegrationError) and error.permanent
    )
