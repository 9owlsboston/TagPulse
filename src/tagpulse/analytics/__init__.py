"""Analytics module framework — plugin base class and registry."""

from abc import ABC, abstractmethod

from tagpulse.events.protocol import Event, Topic


class AnalyticsModule(ABC):
    """Base class all analytics modules must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique module name (e.g. 'read_frequency')."""

    @property
    @abstractmethod
    def subscribed_topics(self) -> list[Topic]:
        """EventBus topics this module listens to."""

    @abstractmethod
    async def on_event(self, event: Event) -> None:
        """Process a single event. Called by the background worker."""

    async def start(self) -> None:
        """Optional lifecycle hook — called once on app startup."""

    async def stop(self) -> None:
        """Optional lifecycle hook — called on app shutdown."""