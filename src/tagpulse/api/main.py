"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(
    title="TagPulse",
    description="IoT platform for RFID tag readers and sensor data",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
