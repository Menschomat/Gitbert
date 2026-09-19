"""Security and scoping exceptions."""


class SecurityScopeViolationError(Exception):
    """Raised when an operation violates MR security boundaries or file allowlists."""

    pass
