class InteropError(Exception):
    """Expected, user-actionable harness failure."""


class ConfigurationError(InteropError):
    """Invalid harness or scenario configuration."""


class CapabilityError(InteropError):
    """A requested suite is unsupported by a signer backend."""


class CheckoutError(InteropError):
    """A source checkout is missing or at an unexpected revision."""
