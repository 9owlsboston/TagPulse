"""Background workers (Sprint 15b Phase E).

Long-lived periodic scans that don't fit the analytics-module event-driven
contract. Each worker manages its own asyncio task lifecycle (``start`` /
``stop``) and is wired into ``main.lifespan``.
"""
