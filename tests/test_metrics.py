"""Tests for metrics module."""
import os
import pytest
from unittest.mock import patch
from src.libs.metrics import _sanitize_prometheus_name, ObsByClaraMetricsSender


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
