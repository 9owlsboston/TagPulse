"""MQTT subscriber — connects to broker and ingests tag read and status messages."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import aiomqtt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.api.services.device_service import DeviceService
from tagpulse.api.services.telemetry_model_service import TelemetryModelService
from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.otel_metrics import (
    mark_mqtt_message_processed,
    mqtt_messages_rejected_counter,
    mqtt_reconnect_attempts_counter,
)
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import (
    DeviceEventPayload,
    DeviceStatusUpdate,
    LocationPayload,
    TagReadCreate,
    TelemetryReading,
    TelemetrySingle,
)
from tagpulse.repositories.timescaledb.devices import TimescaleDeviceRepository
from tagpulse.repositories.timescaledb.tag_reads import TimescaleTagReadRepository
from tagpulse.repositories.timescaledb.telemetry import (
    TimescaleTelemetryReadingsRepository,
)

logger = logging.getLogger(__name__)


def _record_rejection(topic_kind: str, reason: str) -> None:
    """Bump the Sprint 28 C1 mqtt_messages_rejected_total counter.

    Defensive: counter wiring failures must never bring down the message
    loop, so any OTel exception is swallowed.
    """
    try:
        mqtt_messages_rejected_counter.add(1, {"topic_kind": topic_kind, "reason": reason})
    except Exception:  # noqa: BLE001
        logger.exception(
            "failed to record MQTT rejection counter topic_kind=%s reason=%s",
            topic_kind,
            reason,
        )


def _classify_connect_error(exc: BaseException) -> str:
    """Map an exception from aiomqtt.Client connect/recv to a low-cardinality reason label."""
    name = type(exc).__name__.lower()
    if "auth" in name or "unauthor" in name:
        return "auth_failed"
    if "timeout" in name:
        return "timeout"
    if "refused" in name or "connection" in name:
        return "connection_refused"
    return "other"


# Wildcard catches all per-device topic suffixes — handler branches on suffix.
TOPIC_FILTER = "tenants/+/devices/+/+"
KNOWN_SUFFIXES = {"tag-reads", "status", "telemetry", "location", "events"}

# Sprint 19: subject-scoped telemetry topic. External integrations
# (TMS, BMS, mobile apps) publish here when they have already resolved
# the subject and don't need the EPC fan-out path. The handler writes
# straight into ``telemetry_readings`` via ``insert``.
SUBJECT_TOPIC_FILTER = "tenants/+/subjects/+/+/telemetry"
SUBJECT_KINDS = {"device", "asset", "lot", "stock_item", "zone"}


def _parse_topic(topic: str) -> tuple[UUID | None, UUID | None, str | None]:
    """Extract tenant_id, device_id, and type from tenant-scoped topic."""
    parts = str(topic).split("/")
    if len(parts) == 5 and parts[0] == "tenants" and parts[2] == "devices":
        try:
            tenant_id = UUID(parts[1])
            device_id = UUID(parts[3])
        except ValueError:
            logger.warning("Invalid UUID in MQTT topic: %s", topic)
            return None, None, None
        return tenant_id, device_id, parts[4]
    return None, None, None


def _parse_subject_topic(
    topic: str,
) -> tuple[UUID | None, str | None, UUID | None]:
    """Parse ``tenants/{tid}/subjects/{kind}/{sid}/telemetry`` (Sprint 19).

    Returns ``(tenant_id, subject_kind, subject_id)`` or all-None when
    the topic does not match the expected shape or carries an unknown
    subject_kind.
    """
    parts = str(topic).split("/")
    if (
        len(parts) == 6
        and parts[0] == "tenants"
        and parts[2] == "subjects"
        and parts[5] == "telemetry"
    ):
        try:
            tenant_id = UUID(parts[1])
            subject_id = UUID(parts[4])
        except ValueError:
            logger.warning("Invalid UUID in MQTT subject topic: %s", topic)
            return None, None, None
        kind = parts[3]
        if kind not in SUBJECT_KINDS:
            logger.warning("Unknown subject_kind in MQTT topic: %s", topic)
            return None, None, None
        return tenant_id, kind, subject_id
    return None, None, None


class MqttSubscriber:
    """Subscribes to MQTT broker with per-message DB sessions."""

    def __init__(
        self,
        host: str,
        port: int,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        username: str | None = None,
        password: str | None = None,
        usage_meter: UsageMeter | None = None,
        use_tls: bool = False,
        tls_ca_path: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._username = username
        self._password = password
        self._usage_meter = usage_meter
        # Sprint 28 C6 — server-TLS to Mosquitto. When ``use_tls`` is
        # True the subscriber builds an aiomqtt ``TLSParameters`` and
        # passes it to ``aiomqtt.Client(tls_params=...)``. An empty
        # ``tls_ca_path`` means "trust the system CA bundle" — fine
        # for certs issued by a public CA, required when using a
        # self-signed cert from KV (the entrypoint writes it to a
        # known path on the worker container).
        self._use_tls = use_tls
        self._tls_ca_path = tls_ca_path or None

    async def run(self) -> None:
        """Connect to broker, subscribe, and process messages until cancelled.

        Sprint 28 C1: wraps the connect-and-consume loop in an outer
        retry with exponential backoff (capped at 30s) so a transient
        broker hiccup does not require a worker restart. Each connect
        attempt increments ``mqtt_reconnect_attempts_total{reason}``
        with reason='startup' on the first attempt and a classified
        error label thereafter.
        """
        backoff = 1.0
        attempt_reason = "startup"
        while True:
            mqtt_reconnect_attempts_counter.add(1, {"reason": attempt_reason})
            logger.info(
                "MQTT subscriber connecting to %s:%d (reason=%s)",
                self._host,
                self._port,
                attempt_reason,
            )
            try:
                tls_params = None
                if self._use_tls:
                    import ssl as _ssl

                    import aiomqtt as _aiomqtt

                    tls_params = _aiomqtt.TLSParameters(
                        ca_certs=self._tls_ca_path,
                        cert_reqs=_ssl.CERT_REQUIRED,
                        tls_version=_ssl.PROTOCOL_TLS_CLIENT,
                    )
                async with aiomqtt.Client(
                    hostname=self._host,
                    port=self._port,
                    username=self._username,
                    password=self._password,
                    tls_params=tls_params,
                ) as client:
                    await client.subscribe(TOPIC_FILTER)
                    await client.subscribe(SUBJECT_TOPIC_FILTER)
                    logger.info(
                        "MQTT subscribed to %s, %s",
                        TOPIC_FILTER,
                        SUBJECT_TOPIC_FILTER,
                    )
                    backoff = 1.0  # successful connect resets backoff
                    async for message in client.messages:
                        # Sprint 31 (#18): a single bad payload must never escape the
                        # message loop. Anything `_handle_message` raises (TypeError
                        # from `**payload` when the body is a list, ValidationError,
                        # repository errors, …) is logged and dropped so the
                        # subscriber keeps consuming. CancelledError still
                        # propagates so graceful shutdown works.
                        try:
                            await self._handle_message(message)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "MQTT message handler raised; dropping and continuing. topic=%s",
                                message.topic,
                            )
                        else:
                            mark_mqtt_message_processed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                attempt_reason = _classify_connect_error(exc)
                logger.exception(
                    "MQTT subscriber loop crashed; reconnecting in %.1fs (reason=%s)",
                    backoff,
                    attempt_reason,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _persist_mqtt_drop(
        self,
        tenant_id: UUID,
        topic_str: str,
        payload: Any,
        topic_kind: str,
        reason: str,
    ) -> None:
        """Sprint 28 C3: persist a schema-level MQTT drop to dead_letter_events.

        Called only from the ``invalid_schema`` branches where the topic
        was parseable enough to know the tenant — keeps row volume bounded
        and lets operators see the actual rejected payload from the admin
        UI / triage runbook. JSON-parse failures stay metric-only by
        design (could spike under broker-flood).
        """
        from tagpulse.models.database import DeadLetterEventModel

        try:
            body: dict[str, Any] = (
                payload if isinstance(payload, dict) else {"raw": str(payload)[:1000]}
            )
            row = DeadLetterEventModel(
                tenant_id=tenant_id,
                topic=topic_str[:50],
                payload=body,
                error_message=f"mqtt {topic_kind} {reason}",
                retry_count=0,
                status="rejected",
                source="mqtt_subscriber",
            )
            async with self._session_factory() as session:
                session.add(row)
                await session.commit()
        except Exception:  # noqa: BLE001
            # Persistence is best-effort; a DB outage must not stall the
            # message loop. Counter + log already recorded.
            logger.exception(
                "failed to persist MQTT drop topic=%s kind=%s reason=%s",
                topic_str,
                topic_kind,
                reason,
            )

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Route message to the appropriate handler with a fresh DB session."""
        # Sprint 19: route the subject-scoped topic family first so its
        # 6-segment shape is matched before falling through to the
        # legacy 5-segment device topics.
        s_tenant_id, subject_kind, subject_id = _parse_subject_topic(str(message.topic))
        if s_tenant_id is not None and subject_kind is not None and subject_id is not None:
            await self._handle_subject_telemetry(s_tenant_id, subject_kind, subject_id, message)
            return

        tenant_id, device_id, topic_type = _parse_topic(str(message.topic))
        if tenant_id is None or device_id is None or topic_type is None:
            logger.warning("Skipping message with unparseable topic: %s", message.topic)
            _record_rejection("unparseable", "invalid_topic")
            return
        if topic_type not in KNOWN_SUFFIXES:
            logger.warning(
                "Unknown topic suffix '%s' for device %s — dropping",
                topic_type,
                device_id,
            )
            _record_rejection("unknown_suffix", "unknown_suffix")
            return

        if topic_type == "tag-reads":
            await self._handle_tag_read(tenant_id, device_id, message)
        elif topic_type == "status":
            await self._handle_status(tenant_id, device_id, message)
        elif topic_type == "telemetry":
            await self._handle_telemetry(tenant_id, device_id, message)
        elif topic_type == "location":
            await self._handle_location(tenant_id, device_id, message)
        elif topic_type == "events":
            await self._handle_device_event(tenant_id, device_id, message)

    async def _handle_tag_read(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        # Sprint 31 (#18, #19): accept any of these shapes the docs and
        # reference clients have shipped over time:
        #   1. {"tag_id": …, …}                       (canonical, smoke pub)
        #   2. {"device_id": …, "tag_id": …, …}       (Pi reference agent)
        #   3. [{...}, {...}]                         (HTTP /tag-reads/batch
        #                                              shape — used in
        #                                              docs/guides/device-
        #                                              developer-guide.md)
        # In every case the topic-derived ``device_id`` wins; any
        # ``device_id`` field on the body is stripped so the
        # ``TagReadCreate(device_id=device_id, **payload)`` call below
        # cannot raise ``TypeError: got multiple values for keyword
        # argument 'device_id'``.
        try:
            raw: Any = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Skipping tag-read with invalid JSON on topic %s", message.topic)
            _record_rejection("tag_read", "invalid_json")
            return

        if isinstance(raw, list):
            items: list[Any] = raw
        elif isinstance(raw, dict):
            items = [raw]
        else:
            logger.warning(
                "Skipping tag-read with non-object/array payload on topic %s: %r",
                message.topic,
                type(raw).__name__,
            )
            _record_rejection("tag_read", "non_dict_payload")
            return

        reads: list[TagReadCreate] = []
        for item in items:
            if not isinstance(item, dict):
                logger.warning(
                    "Skipping tag-read element with non-object shape on topic %s: %r",
                    message.topic,
                    type(item).__name__,
                )
                _record_rejection("tag_read", "non_dict_payload")
                continue
            payload = {k: v for k, v in item.items() if k != "device_id"}
            try:
                reads.append(TagReadCreate(device_id=device_id, **payload))
            except (ValueError, TypeError):
                logger.warning(
                    "Skipping tag-read with invalid schema on topic %s: %s",
                    message.topic,
                    payload,
                )
                _record_rejection("tag_read", "invalid_schema")
                await self._persist_mqtt_drop(
                    tenant_id, str(message.topic), payload, "tag_read", "invalid_schema"
                )
                continue

        if not reads:
            _record_rejection("tag_read", "no_valid_items")
            return

        try:
            async with self._session_factory() as session:
                ingestion_service = self._build_ingestion_service(session)
                for read in reads:
                    await ingestion_service.ingest(tenant_id, read)
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to ingest tag reads from MQTT: device=%s count=%d",
                device_id,
                len(reads),
            )

    async def _handle_status(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping status with invalid JSON on topic %s", message.topic)
            _record_rejection("status", "invalid_json")
            return

        try:
            status = DeviceStatusUpdate(**payload)
        except ValueError:
            logger.warning(
                "Skipping status with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            _record_rejection("status", "invalid_schema")
            await self._persist_mqtt_drop(
                tenant_id, str(message.topic), payload, "status", "invalid_schema"
            )
            return

        try:
            async with self._session_factory() as session:
                repo = TimescaleDeviceRepository(session)
                device_svc = DeviceService(repo=repo)
                await device_svc.update_status(
                    tenant_id,
                    device_id,
                    connection_state=status.connection_state,
                    firmware_version=status.firmware_version,
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to update device status from MQTT: device=%s",
                device_id,
            )

    async def _handle_telemetry(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping telemetry with invalid JSON on topic %s", message.topic)
            _record_rejection("telemetry", "invalid_json")
            return

        if isinstance(payload.get("readings"), list):
            readings_raw: list[dict[str, Any]] = list(payload["readings"])
        else:
            payload.setdefault("device_id", str(device_id))
            try:
                single = TelemetrySingle(**payload)
            except ValueError:
                logger.warning(
                    "Skipping telemetry with invalid schema on topic %s: %s",
                    message.topic,
                    payload,
                )
                _record_rejection("telemetry", "invalid_schema")
                await self._persist_mqtt_drop(
                    tenant_id, str(message.topic), payload, "telemetry", "invalid_schema"
                )
                return
            readings_raw = [
                {
                    "timestamp": single.timestamp,
                    "metric_name": single.metric_name,
                    "metric_value": single.metric_value,
                    "unit": single.unit,
                    "metadata": single.metadata,
                }
            ]
        try:
            readings = [TelemetryReading(**r) for r in readings_raw]
        except ValueError:
            logger.warning("Skipping telemetry with invalid reading schema on %s", message.topic)
            _record_rejection("telemetry", "invalid_schema")
            return

        try:
            async with self._session_factory() as session:
                svc = self._build_telemetry_service(session)
                for reading in readings:
                    await svc.ingest_reading(tenant_id, device_id, reading)
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to ingest telemetry from MQTT: device=%s",
                device_id,
            )

    async def _handle_location(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping location with invalid JSON on topic %s", message.topic)
            _record_rejection("location", "invalid_json")
            return
        payload.setdefault("device_id", str(device_id))
        try:
            location = LocationPayload(**payload)
        except ValueError:
            logger.warning(
                "Skipping location with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            _record_rejection("location", "invalid_schema")
            await self._persist_mqtt_drop(
                tenant_id, str(message.topic), payload, "location", "invalid_schema"
            )
            return
        try:
            async with self._session_factory() as session:
                svc = self._build_telemetry_service(session)
                await svc.ingest_location(tenant_id, location)
                await session.commit()
        except Exception:
            logger.exception("Failed to ingest location from MQTT: device=%s", device_id)

    async def _handle_device_event(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping event with invalid JSON on topic %s", message.topic)
            _record_rejection("event", "invalid_json")
            return
        payload.setdefault("device_id", str(device_id))
        try:
            event = DeviceEventPayload(**payload)
        except ValueError:
            logger.warning(
                "Skipping device-event with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            _record_rejection("event", "invalid_schema")
            await self._persist_mqtt_drop(
                tenant_id, str(message.topic), payload, "event", "invalid_schema"
            )
            return
        try:
            async with self._session_factory() as session:
                svc = self._build_telemetry_service(session)
                await svc.ingest_device_event(tenant_id, event)
                await session.commit()
        except Exception:
            logger.exception("Failed to ingest device event from MQTT: device=%s", device_id)

    async def _handle_subject_telemetry(
        self,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        message: aiomqtt.Message,
    ) -> None:
        """Sprint 19: write subject-scoped telemetry rows directly.

        Payload accepts either a single reading or ``{"readings": [...]}``.
        The reading shape mirrors :class:`TelemetryReading` (the device
        path's body) — subject is taken from the topic, not the body,
        so a misrouted publish cannot smuggle a different subject in.
        """
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Skipping subject telemetry with invalid JSON on %s",
                message.topic,
            )
            _record_rejection("subject_telemetry", "invalid_json")
            return

        if isinstance(payload.get("readings"), list):
            readings_raw: list[dict[str, Any]] = list(payload["readings"])
        else:
            readings_raw = [payload]
        try:
            readings = [TelemetryReading(**r) for r in readings_raw]
        except ValueError:
            logger.warning(
                "Skipping subject telemetry with invalid reading schema on %s",
                message.topic,
            )
            _record_rejection("subject_telemetry", "invalid_schema")
            await self._persist_mqtt_drop(
                tenant_id,
                str(message.topic),
                payload,
                "subject_telemetry",
                "invalid_schema",
            )
            return

        try:
            async with self._session_factory() as session:
                repo = TimescaleTelemetryReadingsRepository(session)
                published: list[tuple[Any, str, float, str | None]] = []
                for reading in readings:
                    metadata = dict(reading.metadata or {})
                    metadata.setdefault("source", "external")
                    row = await repo.insert(
                        tenant_id=tenant_id,
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        timestamp=reading.timestamp,
                        metric_name=reading.metric_name,
                        metric_value=reading.metric_value,
                        unit=reading.unit,
                        source="external",
                        metadata=metadata,
                    )
                    published.append((row, reading.metric_name, reading.metric_value, reading.unit))
                await session.commit()
            # Sprint 20: publish AFTER commit so rule-engine handlers
            # never see a row the caller cannot read back.
            for row, metric_name, metric_value, unit in published:
                try:
                    await self._event_bus.publish(
                        Topic.TELEMETRY_RECORDED,
                        Event(
                            id=row.id,
                            topic=Topic.TELEMETRY_RECORDED,
                            timestamp=row.timestamp,
                            payload={
                                "tenant_id": str(tenant_id),
                                "subject_kind": subject_kind,
                                "subject_id": str(subject_id),
                                "metric_name": metric_name,
                                "metric_value": metric_value,
                                "unit": unit,
                                "device_id": None,
                                "source": "external",
                                "timestamp": row.timestamp.isoformat(),
                            },
                        ),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "telemetry.recorded publish failed for %s/%s metric %s",
                        subject_kind,
                        subject_id,
                        metric_name,
                    )
        except Exception:
            logger.exception(
                "Failed to ingest subject telemetry from MQTT: %s/%s",
                subject_kind,
                subject_id,
            )

    # -- Helpers --

    def _build_telemetry_service(self, session: AsyncSession) -> TelemetryService:
        return TelemetryService(
            repo=TimescaleTelemetryReadingsRepository(session),
            event_bus=self._event_bus,
            model_service=TelemetryModelService(session),
            device_repo=TimescaleDeviceRepository(session),
        )

    def _build_ingestion_service(self, session: AsyncSession) -> IngestionService:
        from tagpulse.repositories.timescaledb.assets import (
            TimescaleAssetTagBindingRepository,
        )
        from tagpulse.repositories.timescaledb.inventory import (
            TimescaleLotRepository,
            TimescaleProductRepository,
            TimescaleStockItemRepository,
            TimescaleStockMovementRepository,
            TimescaleTagDataMappingRepository,
        )
        from tagpulse.repositories.timescaledb.sites_zones import (
            TimescaleZoneRepository,
        )
        from tagpulse.repositories.timescaledb.tenants import (
            TimescaleTenantRepository,
        )

        return IngestionService(
            repo=TimescaleTagReadRepository(session),
            event_bus=self._event_bus,
            device_repo=TimescaleDeviceRepository(session),
            telemetry_service=self._build_telemetry_service(session),
            binding_repo=TimescaleAssetTagBindingRepository(session),
            zone_repo=TimescaleZoneRepository(session),
            product_repo=TimescaleProductRepository(session),
            lot_repo=TimescaleLotRepository(session),
            stock_repo=TimescaleStockItemRepository(session),
            movement_repo=TimescaleStockMovementRepository(session),
            tag_data_mapping_repo=TimescaleTagDataMappingRepository(session),
            tenant_repo=TimescaleTenantRepository(session),
            telemetry_readings_repo=TimescaleTelemetryReadingsRepository(session),
            usage_meter=self._usage_meter,
        )
