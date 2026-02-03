# ObsByClara Prometheus Remote Write Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert ObsByClara metric sender from CloudWatch format to Prometheus Remote Write protobuf format while preserving SigV4 authentication.

**Architecture:** Replace CloudWatch JSON payload with Prometheus protobuf messages, add Snappy compression, update content-type to application/x-protobuf. All SigV4 signing logic remains unchanged.

**Tech Stack:** Python 3.12, Prometheus Remote Write protobuf, python-snappy, AWS SigV4

---

## Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml:19-20`

**Step 1: Add python-snappy dependency**

Edit `pyproject.toml` to add the Snappy compression library:

```toml
[tool.poetry.dependencies]
python = "^3.12"
azure-data-tables = "^12.7.0"
azure-functions = "^1.23.0"
azure-identity = "^1.23.0"
azure-loganalytics = "^0.1.1"
cffi = "^2.0.0"
cryptography = "^46.0.0"
datadog =  "^0.52.0"
python-dateutil = "^2.9.0"
python-snappy = "^0.7.0"
requests = "^2.32.3"
```

**Step 2: Install dependencies**

Run: `poetry install`
Expected: python-snappy installed successfully

**Step 3: Commit dependency changes**

```bash
git add pyproject.toml poetry.lock
git commit -m "deps: add python-snappy for Prometheus compression"
```

---

## Task 2: Create Prometheus Protobuf Definitions

**Files:**
- Create: `src/libs/prometheus_pb2.py`

**Step 1: Create vendored protobuf definitions**

Create `src/libs/prometheus_pb2.py` with minimal Prometheus Remote Write definitions:

```python
"""
Vendored Prometheus Remote Write protobuf definitions.
Based on https://github.com/prometheus/prometheus/blob/main/prompb/remote.proto

This is a minimal implementation containing only what we need for remote write.
"""

# Protobuf message types as Python dataclasses for simplicity
from dataclasses import dataclass, field
from typing import List


@dataclass
class Label:
    """Prometheus label (key-value pair)."""
    name: str
    value: str


@dataclass
class Sample:
    """Prometheus sample (value + timestamp)."""
    value: float
    timestamp: int  # milliseconds since epoch


@dataclass
class TimeSeries:
    """Prometheus time series (labels + samples)."""
    labels: List[Label] = field(default_factory=list)
    samples: List[Sample] = field(default_factory=list)


@dataclass
class WriteRequest:
    """Prometheus Remote Write request."""
    timeseries: List[TimeSeries] = field(default_factory=list)

    def SerializeToString(self) -> bytes:
        """
        Serialize to Prometheus protobuf wire format.

        Wire format (simplified protobuf encoding):
        - WriteRequest: repeated TimeSeries (field 1)
        - TimeSeries: repeated Label (field 1), repeated Sample (field 2)
        - Label: string name (field 1), string value (field 2)
        - Sample: double value (field 1), int64 timestamp (field 2)
        """
        output = bytearray()

        for ts in self.timeseries:
            # Serialize TimeSeries
            ts_bytes = self._serialize_timeseries(ts)
            # Field 1 (timeseries), wire type 2 (length-delimited)
            output.extend(self._encode_key(1, 2))
            output.extend(self._encode_varint(len(ts_bytes)))
            output.extend(ts_bytes)

        return bytes(output)

    def _serialize_timeseries(self, ts: TimeSeries) -> bytes:
        """Serialize a TimeSeries message."""
        output = bytearray()

        # Serialize labels (field 1)
        for label in ts.labels:
            label_bytes = self._serialize_label(label)
            output.extend(self._encode_key(1, 2))
            output.extend(self._encode_varint(len(label_bytes)))
            output.extend(label_bytes)

        # Serialize samples (field 2)
        for sample in ts.samples:
            sample_bytes = self._serialize_sample(sample)
            output.extend(self._encode_key(2, 2))
            output.extend(self._encode_varint(len(sample_bytes)))
            output.extend(sample_bytes)

        return bytes(output)

    def _serialize_label(self, label: Label) -> bytes:
        """Serialize a Label message."""
        output = bytearray()

        # name (field 1, string)
        name_bytes = label.name.encode('utf-8')
        output.extend(self._encode_key(1, 2))
        output.extend(self._encode_varint(len(name_bytes)))
        output.extend(name_bytes)

        # value (field 2, string)
        value_bytes = label.value.encode('utf-8')
        output.extend(self._encode_key(2, 2))
        output.extend(self._encode_varint(len(value_bytes)))
        output.extend(value_bytes)

        return bytes(output)

    def _serialize_sample(self, sample: Sample) -> bytes:
        """Serialize a Sample message."""
        import struct
        output = bytearray()

        # value (field 1, double/fixed64, wire type 1)
        output.extend(self._encode_key(1, 1))
        output.extend(struct.pack('<d', sample.value))

        # timestamp (field 2, int64, wire type 0)
        output.extend(self._encode_key(2, 0))
        output.extend(self._encode_varint(sample.timestamp))

        return bytes(output)

    @staticmethod
    def _encode_key(field_number: int, wire_type: int) -> bytes:
        """Encode protobuf field key (field_number << 3 | wire_type)."""
        return WriteRequest._encode_varint((field_number << 3) | wire_type)

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        """Encode integer as protobuf varint."""
        output = bytearray()
        while value > 0x7f:
            output.append((value & 0x7f) | 0x80)
            value >>= 7
        output.append(value & 0x7f)
        return bytes(output)
```

**Step 2: Verify protobuf module is importable**

Run: `python -c "from src.libs.prometheus_pb2 import WriteRequest, TimeSeries, Label, Sample; print('OK')"`
Expected: "OK"

**Step 3: Commit protobuf definitions**

```bash
git add src/libs/prometheus_pb2.py
git commit -m "feat: add vendored Prometheus protobuf definitions"
```

---

## Task 3: Add Metric Name Sanitization Helper

**Files:**
- Modify: `src/libs/metrics.py:15` (after imports, before factory function)

**Step 1: Write test for metric name sanitization**

Create `tests/test_metrics.py`:

```python
"""Tests for metrics module."""
import pytest
from src.libs.metrics import _sanitize_prometheus_name


class TestPrometheusNameSanitization:
    """Test Prometheus metric/label name sanitization."""

    def test_dots_to_underscores(self):
        """Dots should be converted to underscores."""
        assert _sanitize_prometheus_name("azure.vm.cpu") == "azure_vm_cpu"

    def test_hyphens_to_underscores(self):
        """Hyphens should be converted to underscores."""
        assert _sanitize_prometheus_name("cpu-usage") == "cpu_usage"

    def test_special_chars_removed(self):
        """Special characters should be converted to underscores."""
        assert _sanitize_prometheus_name("metric%name@test") == "metric_name_test"

    def test_leading_digit_prefixed(self):
        """Names starting with digit should be prefixed with underscore."""
        assert _sanitize_prometheus_name("123_metric") == "_123_metric"

    def test_valid_name_unchanged(self):
        """Valid names should not be modified."""
        assert _sanitize_prometheus_name("valid_metric_name") == "valid_metric_name"
        assert _sanitize_prometheus_name("metric123") == "metric123"

    def test_colons_preserved(self):
        """Colons are valid in Prometheus names and should be preserved."""
        assert _sanitize_prometheus_name("metric:subsystem:name") == "metric:subsystem:name"

    def test_multiple_consecutive_underscores(self):
        """Multiple consecutive invalid chars become single underscore."""
        assert _sanitize_prometheus_name("metric@@@name") == "metric_name"

    def test_empty_string(self):
        """Empty string should return underscore."""
        assert _sanitize_prometheus_name("") == "_"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::TestPrometheusNameSanitization -v`
Expected: ImportError or function not found

**Step 3: Implement sanitization function**

Add to `src/libs/metrics.py` after imports (around line 15):

```python
def _sanitize_prometheus_name(name: str) -> str:
    """
    Sanitize metric/label name to follow Prometheus naming conventions.

    Prometheus metric and label names:
    - May contain ASCII letters, digits, underscores, and colons
    - Must match regex [a-zA-Z_:][a-zA-Z0-9_:]*
    - Must not start with digit

    :param name: Original metric or label name
    :return: Sanitized name following Prometheus conventions
    """
    import re

    if not name:
        logger.warning("Empty metric name provided, using '_' as fallback")
        return "_"

    original_name = name

    # Replace invalid characters with underscores
    # Keep only letters, digits, underscores, and colons
    sanitized = re.sub(r'[^a-zA-Z0-9_:]', '_', name)

    # Collapse multiple consecutive underscores into single underscore
    sanitized = re.sub(r'_+', '_', sanitized)

    # Ensure doesn't start with digit
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized

    # Log warning if name was modified
    if sanitized != original_name:
        logger.warning(
            f"Metric name sanitized for Prometheus: '{original_name}' -> '{sanitized}'"
        )

    return sanitized
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::TestPrometheusNameSanitization -v`
Expected: All tests pass

**Step 5: Commit sanitization helper**

```bash
git add src/libs/metrics.py tests/test_metrics.py
git commit -m "feat: add Prometheus name sanitization helper"
```

---

## Task 4: Update ObsByClaraMetricsSender Init Method

**Files:**
- Modify: `src/libs/metrics.py:268-310` (ObsByClaraMetricsSender.__init__)

**Step 1: Write test for init without namespace**

Add to `tests/test_metrics.py`:

```python
from src.libs.metrics import ObsByClaraMetricsSender


class TestObsByClaraInit:
    """Test ObsByClaraMetricsSender initialization."""

    def test_init_required_params(self):
        """Should initialize with required parameters only."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://aps-workspaces.eu-west-1.amazonaws.com/workspaces/ws-123",
            region="eu-west-1",
            service="aps",
            access_key_id="AKIATEST",
            secret_access_key="secret123",
        )

        assert sender.endpoint.endswith("/api/v1/remote_write")
        assert sender.region == "eu-west-1"
        assert sender.service == "aps"
        assert sender.max_retries == 3
        assert not hasattr(sender, 'namespace')

    def test_init_with_session_token(self):
        """Should accept optional session token."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
            session_token="token123",
        )

        assert sender.session_token == "token123"

    def test_init_appends_remote_write_path(self):
        """Should append /api/v1/remote_write if not present."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        assert sender.endpoint == "https://example.com/api/v1/remote_write"

    def test_init_preserves_remote_write_path(self):
        """Should not duplicate /api/v1/remote_write if already present."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com/api/v1/remote_write",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        assert sender.endpoint == "https://example.com/api/v1/remote_write"

    def test_init_validates_required_params(self):
        """Should raise ValueError for missing required parameters."""
        with pytest.raises(ValueError, match="endpoint is required"):
            ObsByClaraMetricsSender(
                endpoint="",
                region="us-east-1",
                service="aps",
                access_key_id="key",
                secret_access_key="secret",
            )
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::TestObsByClaraInit -v`
Expected: Tests fail (signature mismatch, namespace still present)

**Step 3: Update __init__ method signature and implementation**

In `src/libs/metrics.py`, update the ObsByClaraMetricsSender.__init__ method:

```python
def __init__(
    self,
    endpoint: str,
    region: str,
    service: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: str = None,
    max_retries: int = 3,
):
    """
    Initialize the ObsByClara metrics sender with configuration.

    :param endpoint: Full URL of the ObsByClara API endpoint (will append /api/v1/remote_write if not present)
    :param region: AWS region for SigV4 signing
    :param service: AWS service name for SigV4 signing (typically 'aps' for AWS Managed Prometheus)
    :param access_key_id: AWS access key ID
    :param secret_access_key: AWS secret access key
    :param session_token: AWS session token (optional)
    :param max_retries: Maximum number of retry attempts (default: 3)
    """
    logger.info(
        f"Initializing ObsByClara metrics sender with endpoint: {endpoint}, region: {region}"
    )

    if not endpoint:
        raise ValueError("ObsByClara endpoint is required")
    if not region:
        raise ValueError("ObsByClara region is required")
    if not service:
        raise ValueError("ObsByClara service is required")
    if not access_key_id:
        raise ValueError("AWS access key ID is required")
    if not secret_access_key:
        raise ValueError("AWS secret access key is required")

    # Append remote write path if not present
    if not endpoint.endswith("/api/v1/remote_write"):
        endpoint = endpoint.rstrip("/") + "/api/v1/remote_write"

    self.endpoint = endpoint
    self.region = region
    self.service = service
    self.access_key_id = access_key_id
    self.secret_access_key = secret_access_key
    self.session_token = session_token
    self.max_retries = max_retries
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::TestObsByClaraInit -v`
Expected: All tests pass

**Step 5: Commit init method changes**

```bash
git add src/libs/metrics.py tests/test_metrics.py
git commit -m "feat: update ObsByClara init to remove namespace and handle endpoint"
```

---

## Task 5: Update Factory Function

**Files:**
- Modify: `src/libs/metrics.py:48-100` (get_metrics_sender function)

**Step 1: Write test for factory without namespace**

Add to `tests/test_metrics.py`:

```python
import os
from unittest.mock import patch


class TestMetricsSenderFactory:
    """Test get_metrics_sender factory function."""

    def test_factory_creates_obsbyclara_without_namespace(self):
        """Factory should create ObsByClara sender without namespace env var."""
        env_vars = {
            "OBC_ENDPOINT": "https://example.com",
            "OBC_REGION": "eu-west-1",
            "OBC_SERVICE": "aps",
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            from src.libs.metrics import get_metrics_sender
            sender = get_metrics_sender()

            assert isinstance(sender, ObsByClaraMetricsSender)
            assert sender.region == "eu-west-1"
            assert not hasattr(sender, 'namespace')

    def test_factory_obeys_priority_order(self):
        """Factory should prioritize ObsByClara > Datadog > SignalFx."""
        env_vars = {
            "OBC_ENDPOINT": "https://example.com",
            "OBC_REGION": "eu-west-1",
            "OBC_SERVICE": "aps",
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "DD_API_KEY": "dd_key",
            "SFX_TOKEN": "sfx_token",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            from src.libs.metrics import get_metrics_sender
            sender = get_metrics_sender()

            assert isinstance(sender, ObsByClaraMetricsSender)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::TestMetricsSenderFactory -v`
Expected: Tests fail (namespace still being used)

**Step 3: Update factory function**

In `src/libs/metrics.py`, update the get_metrics_sender function to remove namespace handling:

```python
def get_metrics_sender() -> "MetricsSender":
    """
    Factory function to create and return the appropriate metrics sender based on environment variables.

    Priority order: ObsByClara > Datadog > SignalFx

    :return: An instance of a MetricsSender implementation
    """
    # Check for ObsByClara configuration
    obc_endpoint = os.environ.get("OBC_ENDPOINT")
    obc_region = os.environ.get("OBC_REGION")
    obc_service = os.environ.get("OBC_SERVICE")
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_session_token = os.environ.get("AWS_SESSION_TOKEN")
    obc_max_retries = int(os.environ.get("OBC_MAX_RETRIES", "3"))

    # Check for Datadog configuration
    dd_api_key = os.environ.get("DD_API_KEY")
    dd_api_host = os.environ.get("DD_API_HOST")

    # Check for SignalFx configuration
    sfx_token = os.environ.get("SFX_TOKEN")
    sfx_realm = os.environ.get("SFX_REALM")

    # Prioritize ObsByClara if available
    if obc_endpoint and obc_region and obc_service and aws_access_key_id and aws_secret_access_key:
        logger.info("Using ObsByClara metrics sender")
        obc_config = {
            "endpoint": obc_endpoint,
            "region": obc_region,
            "service": obc_service,
            "access_key_id": aws_access_key_id,
            "secret_access_key": aws_secret_access_key,
            "max_retries": obc_max_retries,
        }
        if aws_session_token:
            obc_config["session_token"] = aws_session_token
        return ObsByClaraMetricsSender(**obc_config)
    elif dd_api_key:
        logger.info("Using Datadog metrics sender")
        dd_config = {"api_key": dd_api_key}
        if dd_api_host:
            dd_config["api_host"] = dd_api_host
        return DatadogMetricsSender(**dd_config)
    elif sfx_token:
        logger.info("Using SignalFx metrics sender")
        sfx_config = {
            "token": sfx_token,
        }
        if sfx_realm:
            sfx_config["realm"] = sfx_realm
        return SignalFxMetricsSender(**sfx_config)
    else:
        raise ValueError(
            "No metrics backend configuration found. "
            "Please provide either ObsByClara (OBC_ENDPOINT, OBC_REGION, OBC_SERVICE, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY), "
            "Datadog (DD_API_KEY), or SignalFx (SFX_TOKEN) credentials.",
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::TestMetricsSenderFactory -v`
Expected: All tests pass

**Step 5: Commit factory changes**

```bash
git add src/libs/metrics.py tests/test_metrics.py
git commit -m "feat: update factory to remove namespace handling"
```

---

## Task 6: Implement Prometheus Payload Builder

**Files:**
- Modify: `src/libs/metrics.py` (add new method before send_metrics)

**Step 1: Write test for Prometheus payload building**

Add to `tests/test_metrics.py`:

```python
from datetime import datetime
from src.libs.prometheus_pb2 import WriteRequest, TimeSeries, Label, Sample


class TestPrometheusPayloadBuilder:
    """Test Prometheus protobuf payload building."""

    def test_build_write_request_single_metric(self):
        """Should build WriteRequest for single metric."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        metric_name = "test_metric"
        timestamp = datetime(2026, 1, 1, 12, 0, 0)
        values = [
            (timestamp, 42.5, {"env": "prod", "host": "server1"}),
        ]

        write_request = sender._build_prometheus_write_request(metric_name, values)

        assert isinstance(write_request, WriteRequest)
        assert len(write_request.timeseries) == 1

        ts = write_request.timeseries[0]
        assert len(ts.labels) == 3  # __name__ + env + host
        assert len(ts.samples) == 1

        # Check __name__ label
        name_labels = [l for l in ts.labels if l.name == "__name__"]
        assert len(name_labels) == 1
        assert name_labels[0].value == "test_metric"

        # Check dimension labels
        env_labels = [l for l in ts.labels if l.name == "env"]
        assert len(env_labels) == 1
        assert env_labels[0].value == "prod"

        # Check sample
        assert ts.samples[0].value == 42.5
        assert ts.samples[0].timestamp == int(timestamp.timestamp() * 1000)

    def test_build_write_request_multiple_metrics_same_dimensions(self):
        """Should create separate TimeSeries for each value even with same dimensions."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        metric_name = "test_metric"
        timestamp1 = datetime(2026, 1, 1, 12, 0, 0)
        timestamp2 = datetime(2026, 1, 1, 12, 1, 0)
        values = [
            (timestamp1, 10.0, {"env": "prod"}),
            (timestamp2, 20.0, {"env": "prod"}),
        ]

        write_request = sender._build_prometheus_write_request(metric_name, values)

        assert len(write_request.timeseries) == 2

    def test_build_write_request_sanitizes_metric_name(self):
        """Should sanitize metric names to Prometheus format."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        metric_name = "azure.vm.cpu-usage"
        timestamp = datetime(2026, 1, 1, 12, 0, 0)
        values = [(timestamp, 50.0, {})]

        write_request = sender._build_prometheus_write_request(metric_name, values)

        ts = write_request.timeseries[0]
        name_labels = [l for l in ts.labels if l.name == "__name__"]
        assert name_labels[0].value == "azure_vm_cpu_usage"

    def test_build_write_request_sanitizes_label_names(self):
        """Should sanitize label names to Prometheus format."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        metric_name = "test_metric"
        timestamp = datetime(2026, 1, 1, 12, 0, 0)
        values = [(timestamp, 50.0, {"resource-group": "rg1", "vm.name": "vm01"})]

        write_request = sender._build_prometheus_write_request(metric_name, values)

        ts = write_request.timeseries[0]
        label_names = [l.name for l in ts.labels]
        assert "resource_group" in label_names
        assert "vm_name" in label_names
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::TestPrometheusPayloadBuilder -v`
Expected: Method not found

**Step 3: Implement _build_prometheus_write_request method**

Add this method to ObsByClaraMetricsSender class in `src/libs/metrics.py` (before send_metrics):

```python
def _build_prometheus_write_request(
    self, name: str, values: List[Tuple[datetime, float, Dict[str, str]]]
) -> "WriteRequest":
    """
    Build a Prometheus WriteRequest from metric data.

    :param name: Metric name
    :param values: List of (timestamp, value, dimensions) tuples
    :return: Prometheus WriteRequest object
    """
    from libs.prometheus_pb2 import WriteRequest, TimeSeries, Label, Sample

    write_request = WriteRequest()

    # Sanitize metric name
    sanitized_name = _sanitize_prometheus_name(name)

    # Create a TimeSeries for each metric value
    for dt, value, dimensions in values:
        ts = TimeSeries()

        # Add __name__ label
        ts.labels.append(Label(name="__name__", value=sanitized_name))

        # Add dimension labels (sanitize label names)
        for dim_name, dim_value in sorted(dimensions.items()):
            sanitized_label_name = _sanitize_prometheus_name(dim_name)
            ts.labels.append(Label(name=sanitized_label_name, value=str(dim_value)))

        # Add sample (convert timestamp to milliseconds)
        timestamp_ms = int(dt.timestamp() * 1000)
        ts.samples.append(Sample(value=value, timestamp=timestamp_ms))

        write_request.timeseries.append(ts)

    logger.debug(
        f"Built Prometheus WriteRequest with {len(write_request.timeseries)} time series"
    )

    return write_request
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::TestPrometheusPayloadBuilder -v`
Expected: All tests pass

**Step 5: Commit payload builder**

```bash
git add src/libs/metrics.py tests/test_metrics.py
git commit -m "feat: add Prometheus WriteRequest builder"
```

---

## Task 7: Update send_metrics Method

**Files:**
- Modify: `src/libs/metrics.py` (ObsByClaraMetricsSender.send_metrics method)

**Step 1: Write test for send_metrics with Prometheus payload**

Add to `tests/test_metrics.py`:

```python
from unittest.mock import Mock, patch
import snappy


class TestObsBy ClaraSendMetrics:
    """Test ObsByClara send_metrics method with Prometheus format."""

    def test_send_metrics_creates_protobuf_payload(self):
        """Should create and compress Prometheus protobuf payload."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        timestamp = datetime(2026, 1, 1, 12, 0, 0)
        values = [(timestamp, 42.5, {"env": "prod"})]

        with patch.object(sender, '_send_signed_request') as mock_send:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()
            mock_send.return_value = mock_response

            sender.send_metrics("test_metric", values)

            # Verify _send_signed_request was called
            assert mock_send.called
            call_args = mock_send.call_args[0]
            payload_bytes = call_args[0]

            # Verify payload is bytes (compressed)
            assert isinstance(payload_bytes, bytes)

            # Verify payload can be decompressed
            decompressed = snappy.decompress(payload_bytes)
            assert isinstance(decompressed, bytes)
            assert len(decompressed) > 0

    def test_send_metrics_handles_empty_values(self):
        """Should handle empty values list without making request."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
        )

        with patch.object(sender, '_send_signed_request') as mock_send:
            sender.send_metrics("test_metric", [])

            # Should not make request
            assert not mock_send.called

    def test_send_metrics_retries_on_failure(self):
        """Should retry with exponential backoff on transient failures."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="key",
            secret_access_key="secret",
            max_retries=2,
        )

        timestamp = datetime(2026, 1, 1, 12, 0, 0)
        values = [(timestamp, 42.5, {"env": "prod"})]

        with patch.object(sender, '_send_signed_request') as mock_send:
            with patch('time.sleep') as mock_sleep:
                # First two calls fail, third succeeds
                mock_response_fail = Mock()
                mock_response_fail.status_code = 500
                mock_response_fail.raise_for_status.side_effect = Exception("500 error")

                mock_response_success = Mock()
                mock_response_success.status_code = 200
                mock_response_success.raise_for_status = Mock()

                mock_send.side_effect = [
                    mock_response_fail,
                    mock_response_fail,
                    mock_response_success,
                ]

                sender.send_metrics("test_metric", values)

                # Verify retries
                assert mock_send.call_count == 3
                assert mock_sleep.call_count == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::TestObsByClaraSendMetrics -v`
Expected: Tests fail (still using CloudWatch format)

**Step 3: Update send_metrics method**

Replace the send_metrics method in ObsByClaraMetricsSender:

```python
def send_metrics(
    self, name: str, values: List[Tuple[datetime, float, Dict[str, str]]]
) -> None:
    """
    Send metrics to ObsByClara using AWS SigV4 signed requests with Prometheus Remote Write format.

    :param name: Name of the metric
    :param values: List of timestamp, value and dimensions tuples
    :return: None
    """
    import snappy

    if not values:
        logger.warning(f"No metrics data to send for {name}")
        return

    # Build Prometheus WriteRequest
    write_request = self._build_prometheus_write_request(name, values)

    # Serialize to protobuf
    try:
        payload_bytes = write_request.SerializeToString()
        logger.debug(f"Serialized protobuf payload: {len(payload_bytes)} bytes")
    except Exception as e:
        logger.error(f"Failed to serialize Prometheus protobuf: {e}")
        raise

    # Compress with Snappy
    try:
        compressed_payload = snappy.compress(payload_bytes)
        logger.debug(
            f"Compressed payload: {len(payload_bytes)} -> {len(compressed_payload)} bytes"
        )
    except Exception as e:
        logger.error(f"Failed to compress payload with Snappy: {e}")
        raise

    # Retry logic with exponential backoff
    for attempt in range(self.max_retries + 1):
        try:
            # Sign and send the request
            response = self._send_signed_request(compressed_payload)
            response.raise_for_status()
            logger.info(
                f"Successfully sent {name} metrics to ObsByClara (attempt {attempt + 1})"
            )
            break
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as e:
            if attempt < self.max_retries:
                delay = 2**attempt
                logger.warning(
                    f"Attempt {attempt + 1} failed with {type(e).__name__}: {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
                continue
            logger.error(
                f"Failed to send metrics to ObsByClara after {self.max_retries + 1} attempts"
            )
            raise
        except requests.exceptions.HTTPError as e:
            # Retry on 5xx errors or 429 (rate limiting)
            if e.response.status_code >= 500 or e.response.status_code == 429:
                if attempt < self.max_retries:
                    delay = 2**attempt
                    logger.warning(
                        f"HTTP {e.response.status_code} error on attempt {attempt + 1}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
            logger.error(f"HTTP error sending metrics to ObsByClara: {e}")
            raise
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::TestObsByClaraSendMetrics -v`
Expected: All tests pass

**Step 5: Commit send_metrics changes**

```bash
git add src/libs/metrics.py tests/test_metrics.py
git commit -m "feat: update send_metrics to use Prometheus protobuf format"
```

---

## Task 8: Update _send_signed_request Method

**Files:**
- Modify: `src/libs/metrics.py` (ObsByClaraMetricsSender._send_signed_request method)

**Step 1: Write test for SigV4 signing with protobuf content-type**

Add to `tests/test_metrics.py`:

```python
class TestObsByClaraSigV4Signing:
    """Test SigV4 signing with Prometheus protobuf."""

    def test_send_signed_request_uses_protobuf_content_type(self):
        """Should use application/x-protobuf content type."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="AKIATEST",
            secret_access_key="secretkey",
        )

        test_payload = b"test_protobuf_bytes"

        with patch('src.libs.metrics.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            sender._send_signed_request(test_payload)

            # Verify request was made
            assert mock_post.called
            call_kwargs = mock_post.call_args[1]

            # Verify headers
            headers = call_kwargs['headers']
            assert headers['Content-Type'] == 'application/x-protobuf'
            assert 'Authorization' in headers
            assert 'AWS4-HMAC-SHA256' in headers['Authorization']
            assert 'X-Amz-Date' in headers

            # Verify payload is bytes
            assert call_kwargs['data'] == test_payload

    def test_send_signed_request_includes_session_token(self):
        """Should include X-Amz-Security-Token when session token present."""
        sender = ObsByClaraMetricsSender(
            endpoint="https://example.com",
            region="us-east-1",
            service="aps",
            access_key_id="AKIATEST",
            secret_access_key="secretkey",
            session_token="session123",
        )

        test_payload = b"test"

        with patch('src.libs.metrics.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            sender._send_signed_request(test_payload)

            headers = mock_post.call_args[1]['headers']
            assert headers['X-Amz-Security-Token'] == 'session123'
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::TestObsByClaraSigV4Signing -v`
Expected: Tests fail (content-type still application/json)

**Step 3: Update _send_signed_request method**

Replace the _send_signed_request method in ObsByClaraMetricsSender:

```python
def _send_signed_request(self, payload: bytes) -> requests.Response:
    """
    Send an AWS SigV4 signed HTTP POST request with Prometheus protobuf payload.

    :param payload: Compressed protobuf payload as bytes
    :return: HTTP response
    """
    # Parse endpoint URL
    from urllib.parse import urlparse

    parsed_url = urlparse(self.endpoint)
    host = parsed_url.netloc
    canonical_uri = parsed_url.path if parsed_url.path else "/"

    # Request parameters
    method = "POST"
    content_type = "application/x-protobuf"

    # Create timestamps
    t = datetime.utcnow()
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")

    # Create canonical request
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical_headers = f"content-type:{content_type}\nhost:{host}\nx-amz-date:{amz_date}\n"
    signed_headers = "content-type;host;x-amz-date"

    # Add session token to headers if present
    if self.session_token:
        canonical_headers += f"x-amz-security-token:{self.session_token}\n"
        signed_headers += ";x-amz-security-token"

    canonical_request = (
        f"{method}\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    logger.debug(f"Canonical request: {canonical_request}")

    # Create string to sign
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{self.region}/{self.service}/aws4_request"
    canonical_request_hash = hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n{canonical_request_hash}"
    )

    logger.debug(f"String to sign: {string_to_sign}")

    # Calculate signature
    signing_key = _get_signature_key(
        self.secret_access_key, date_stamp, self.region, self.service
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # Build authorization header
    authorization_header = (
        f"{algorithm} Credential={self.access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    # Prepare headers
    headers = {
        "Content-Type": content_type,
        "Host": host,
        "X-Amz-Date": amz_date,
        "Authorization": authorization_header,
    }

    if self.session_token:
        headers["X-Amz-Security-Token"] = self.session_token

    logger.debug(f"Request headers: {headers}")

    # Send request
    response = requests.post(self.endpoint, headers=headers, data=payload)
    return response
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::TestObsByClaraSigV4Signing -v`
Expected: All tests pass

**Step 5: Commit _send_signed_request changes**

```bash
git add src/libs/metrics.py tests/test_metrics.py
git commit -m "feat: update SigV4 signing to use protobuf content-type"
```

---

## Task 9: Run Full Test Suite

**Files:**
- Test: `tests/test_metrics.py`

**Step 1: Run all tests**

Run: `pytest tests/test_metrics.py -v`
Expected: All tests pass

**Step 2: Run tests with coverage**

Run: `pytest tests/test_metrics.py --cov=src.libs.metrics --cov-report=term-missing`
Expected: High coverage (>90%)

**Step 3: Fix any failing tests**

If any tests fail, fix them and re-run until all pass.

**Step 4: Commit any test fixes**

```bash
git add tests/test_metrics.py
git commit -m "test: ensure all tests pass"
```

---

## Task 10: Update Documentation

**Files:**
- Modify: `README.md:36-46` (Metrics Backend Configuration section)

**Step 1: Update README to remove namespace reference**

Update the ObsByClara configuration section in `README.md`:

```markdown
#### Metrics Backend Configuration (one backend required)

##### ObsByClara/Prometheus Configuration
* **OBC_ENDPOINT** (required for ObsByClara): The ObsByClara API endpoint URL (e.g., AWS Managed Prometheus workspace URL)
* **OBC_REGION** (required for ObsByClara): AWS region for SigV4 signing (e.g., `eu-west-1`)
* **OBC_SERVICE** (required for ObsByClara): AWS service name for SigV4 signing (typically `aps` for AWS Managed Prometheus)
* **AWS_ACCESS_KEY_ID** (required for ObsByClara): AWS access key ID for authentication
* **AWS_SECRET_ACCESS_KEY** (required for ObsByClara): AWS secret access key for authentication
* **AWS_SESSION_TOKEN** (optional): AWS session token for temporary credentials
* **OBC_MAX_RETRIES** (optional, defaults to `3`): Maximum number of retry attempts for failed requests

**Note:** ObsByClara sends metrics in Prometheus Remote Write format. The endpoint URL should point to a Prometheus-compatible remote write endpoint (e.g., AWS Managed Prometheus). The `/api/v1/remote_write` path will be appended automatically if not present.

##### Splunk Observability Configuration
* **SFX_TOKEN** (required for Splunk): The Splunk Observability token for metric sending
* **SFX_REALM** (optional, defaults to `eu0`): Splunk realm (region) to use for metric sending

##### Datadog Configuration
* **DD_API_KEY** (required for Datadog): The Datadog API key for metric sending
* **DD_API_HOST** (optional, defaults to `https://api.datadoghq.eu`): Datadog API host
```

**Step 2: Add note about metric format conversion**

Add a new section after "How it works":

```markdown
### Metric Format

Metrics are sent to the configured backend in the native format for that backend:

- **ObsByClara/Prometheus**: Metrics are sent using Prometheus Remote Write protocol with protobuf serialization and Snappy compression. Metric names and label names are automatically sanitized to follow Prometheus naming conventions (replacing dots and hyphens with underscores, ensuring names start with a letter or underscore).

- **Datadog**: Metrics are sent using Datadog's native gauge metric format with tags.

- **Splunk Observability**: Metrics are sent using SignalFx's gauge metric format with dimensions.

All backends receive the same metric data (name, value, timestamp, dimensions), just in different wire formats.
```

**Step 3: Commit documentation updates**

```bash
git add README.md
git commit -m "docs: update ObsByClara config for Prometheus format"
```

---

## Task 11: Merge feat/obc Branch and Update

**Files:**
- Merge from: `feat/obc` branch
- Merge to: current branch

**Step 1: Check current branch**

Run: `git branch --show-current`
Expected: Shows current branch name (probably `dev`)

**Step 2: Merge feat/obc into current branch**

Run: `git merge feat/obc`
Expected: Merge completes (may have conflicts in metrics.py)

**Step 3: Resolve conflicts if any**

If conflicts in `src/libs/metrics.py`:
- Keep the Prometheus implementation (new code)
- Discard CloudWatch payload code from feat/obc
- Ensure no duplicate functions

**Step 4: Run tests after merge**

Run: `pytest tests/test_metrics.py -v`
Expected: All tests still pass

**Step 5: Commit merge**

```bash
git add .
git commit -m "chore: merge feat/obc and complete Prometheus migration"
```

---

## Task 12: Integration Testing

**Files:**
- Create: `test_obc_integration.py` (optional, for manual testing)

**Step 1: Create integration test script**

Create `test_obc_integration.py`:

```python
#!/usr/bin/env python3
"""
Integration test for ObsByClara Prometheus sender.
Requires real AWS credentials and AMP workspace.

Set these environment variables:
- OBC_ENDPOINT: AMP workspace URL
- OBC_REGION: AWS region
- OBC_SERVICE: aps
- AWS_ACCESS_KEY_ID: AWS key
- AWS_SECRET_ACCESS_KEY: AWS secret
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, 'src')

from libs.metrics import get_metrics_sender


def test_integration():
    """Send test metric to real AMP workspace."""

    # Check required env vars
    required = ['OBC_ENDPOINT', 'OBC_REGION', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY']
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        print(f"Missing required env vars: {missing}")
        return 1

    try:
        # Create sender
        sender = get_metrics_sender()
        print(f"Created sender: {type(sender).__name__}")

        # Send test metric
        metric_name = "fame_test_metric"
        timestamp = datetime.utcnow()
        values = [
            (timestamp, 42.0, {"test": "integration", "env": "dev"}),
        ]

        print(f"Sending test metric: {metric_name}")
        sender.send_metrics(metric_name, values)
        print("✓ Metric sent successfully!")

        print("\nVerify in AWS console:")
        print(f"- Open CloudWatch > Prometheus workspaces")
        print(f"- Query: {metric_name}")
        print(f"- Should see value 42.0 with labels test=integration, env=dev")

        return 0

    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(test_integration())
```

**Step 2: Make script executable**

Run: `chmod +x test_obc_integration.py`
Expected: Script is executable

**Step 3: Document integration testing**

Add note to README or create `docs/TESTING.md` with instructions for integration testing.

**Step 4: Commit integration test**

```bash
git add test_obc_integration.py
git commit -m "test: add integration test script for ObsByClara"
```

---

## Task 13: Final Verification

**Files:**
- All modified files

**Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests pass

**Step 2: Check code formatting**

Run: `poetry run ruff check src/ tests/`
Expected: No errors

**Step 3: Run type checking**

Run: `poetry run pyright src/`
Expected: No errors (or acceptable warnings)

**Step 4: Verify no CloudWatch references remain**

Run: `grep -r "CloudWatch\|Namespace.*Metric" src/libs/metrics.py`
Expected: No matches (or only in comments/docstrings noting the change)

**Step 5: Create final summary commit**

```bash
git add .
git commit -m "feat: complete ObsByClara Prometheus Remote Write migration

- Replace CloudWatch JSON format with Prometheus protobuf
- Add Snappy compression for remote write protocol
- Update SigV4 signing for protobuf content-type
- Remove namespace parameter (not used in Prometheus)
- Add metric/label name sanitization
- Update documentation and tests
- All tests passing

Co-Authored-By: Claude (claude-sonnet-4.5) <noreply@anthropic.com>"
```

---

## Success Criteria

- ✅ ObsByClara sends metrics in Prometheus Remote Write protobuf format
- ✅ Snappy compression applied to payloads
- ✅ SigV4 authentication works with protobuf
- ✅ Metric names sanitized (dots → underscores)
- ✅ Label names sanitized
- ✅ All retry logic preserved
- ✅ Factory function removes namespace handling
- ✅ Documentation updated
- ✅ All tests pass
- ✅ No breaking changes to Datadog/SignalFx senders

## Notes

- The vendored protobuf implementation uses manual wire format encoding to avoid large dependencies
- Timestamp conversion: datetime → milliseconds since epoch (Prometheus requirement)
- Each metric value creates a separate TimeSeries (Prometheus best practice)
- SigV4 signing logic remains identical except for content-type header change
