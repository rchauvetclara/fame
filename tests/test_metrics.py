"""Tests for metrics module."""
import os
import pytest
import snappy
from datetime import datetime
from unittest.mock import Mock, patch
from src.libs.metrics import _sanitize_prometheus_name, ObsByClaraMetricsSender
from src.libs.prometheus_pb2 import WriteRequest


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
        name_labels = [label for label in ts.labels if label.name == "__name__"]
        assert len(name_labels) == 1
        assert name_labels[0].value == "test_metric"

        # Check dimension labels
        env_labels = [label for label in ts.labels if label.name == "env"]
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
        name_labels = [label for label in ts.labels if label.name == "__name__"]
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
        label_names = [label.name for label in ts.labels]
        assert "resource_group" in label_names
        assert "vm_name" in label_names


class TestObsByClaraSendMetrics:
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
        import requests

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
                # Create HTTPError for 500 response
                mock_response_fail = Mock()
                mock_response_fail.status_code = 500
                http_error = requests.exceptions.HTTPError(response=mock_response_fail)
                http_error.response = mock_response_fail

                # First two calls fail, third succeeds
                def raise_http_error():
                    raise http_error

                mock_response_fail.raise_for_status = raise_http_error

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
