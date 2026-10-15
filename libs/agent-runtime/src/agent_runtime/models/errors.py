class GatewayError(Exception):
    """The gateway could not answer after retries (network, 5xx, 429) or refused the request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CapabilityError(Exception):
    """A request needs a capability (tool calling, structured output) the model lacks."""


class StructuredOutputError(Exception):
    """The model's reply did not match the response schema, even after one repair attempt."""
