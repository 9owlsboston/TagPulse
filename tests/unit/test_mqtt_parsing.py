"""Unit tests for the MQTT message parsing logic."""

from uuid import uuid4

from tagpulse.ingestion.mqtt_subscriber import _parse_topic


class TestMqttTopicParsing:
    def test_valid_tag_reads_topic(self) -> None:
        tenant_id = uuid4()
        device_id = uuid4()
        tid, did, topic_type = _parse_topic(f"tenants/{tenant_id}/devices/{device_id}/tag-reads")
        assert tid == tenant_id
        assert did == device_id
        assert topic_type == "tag-reads"

    def test_valid_status_topic(self) -> None:
        tenant_id = uuid4()
        device_id = uuid4()
        tid, did, topic_type = _parse_topic(f"tenants/{tenant_id}/devices/{device_id}/status")
        assert tid == tenant_id
        assert did == device_id
        assert topic_type == "status"

    def test_invalid_uuid(self) -> None:
        tid, did, topic_type = _parse_topic("tenants/not-a-uuid/devices/also-bad/tag-reads")
        assert tid is None
        assert did is None

    def test_wrong_prefix(self) -> None:
        tenant_id = uuid4()
        device_id = uuid4()
        tid, did, topic_type = _parse_topic(f"orgs/{tenant_id}/devices/{device_id}/tag-reads")
        assert tid is None

    def test_too_few_parts(self) -> None:
        tid, did, topic_type = _parse_topic("devices/tag-reads")
        assert tid is None

    def test_old_format_rejected(self) -> None:
        device_id = uuid4()
        tid, did, topic_type = _parse_topic(f"devices/{device_id}/tag-reads")
        assert tid is None
