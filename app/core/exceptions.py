"""Custom exception hierarchy for SupportBundleAI."""


class SupportBundleError(Exception):
    """Base exception for application errors."""


class ConfigurationError(SupportBundleError):
    """Application configuration is invalid."""


class CollectorError(SupportBundleError):
    """A diagnostic collector failed."""


class PolicyViolationError(SupportBundleError):
    """A requested action violates collection policy."""


class SanitizationError(SupportBundleError):
    """Collected evidence could not be sanitized."""


class BundleCreationError(SupportBundleError):
    """A diagnostic bundle could not be created."""


class AIProviderError(SupportBundleError):
    """An AI provider request failed."""
