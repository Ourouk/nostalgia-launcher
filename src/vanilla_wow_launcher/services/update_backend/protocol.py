"""Structural interfaces shared by client update transfer backends."""

from typing import Any, Protocol


class UpdateBackend(Protocol):
    """Common bulk-transfer capability for an update backend.

    HTTP and BitTorrent retain their different verification and discovery
    workflows. This protocol intentionally covers only their shared lifecycle;
    concrete backends may expose different download arguments.
    """

    def download(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Transfer data using backend-specific arguments."""

    def cancel(self) -> None:
        """Request cancellation of an active transfer."""
