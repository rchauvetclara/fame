#!/usr/bin/env python3
"""
Simple test script to validate ObsByClaraMetricsSender implementation.
This is a basic validation script, not a comprehensive test suite.
"""

import sys
from datetime import datetime
from unittest.mock import Mock, patch
import json

# Add src to path
sys.path.insert(0, 'src')

from libs.metrics import ObsByClaraMetricsSender, _sign, _get_signature_key


def test_sign_functions():
    """Test the AWS SigV4 signing utility functions."""
    print("Testing SigV4 signing functions...")

    # Test _sign function
    key = b"test_key"
    msg = "test_message"
    signature = _sign(key, msg)
    assert isinstance(signature, bytes)
    assert len(signature) == 32  # SHA256 produces 32 bytes
    print("✓ _sign function works correctly")

    # Test _get_signature_key function
    secret_key = "test_secret"
    date_stamp = "20260129"
    region = "eu-west-1"
    service = "execute-api"
    signing_key = _get_signature_key(secret_key, date_stamp, region, service)
    assert isinstance(signing_key, bytes)
    assert len(signing_key) == 32  # SHA256 produces 32 bytes
    print("✓ _get_signature_key function works correctly")


def test_obc_init():
    """Test ObsByClaraMetricsSender initialization."""
    print("\nTesting ObsByClaraMetricsSender initialization...")

    # Test valid initialization
    sender = ObsByClaraMetricsSender(
        endpoint="https://example.execute-api.eu-west-1.amazonaws.com/prod/metrics",
        region="eu-west-1",
        service="execute-api",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    assert sender.endpoint == "https://example.execute-api.eu-west-1.amazonaws.com/prod/metrics"
    assert sender.region == "eu-west-1"
    assert sender.service == "execute-api"
    assert sender.namespace == "CustomMetrics"
    assert sender.max_retries == 3
    print("✓ Initialization with required parameters works")

    # Test initialization with optional parameters
    sender = ObsByClaraMetricsSender(
        endpoint="https://example.com",
        region="us-east-1",
        service="aps",
        access_key_id="test_key",
        secret_access_key="test_secret",
        session_token="test_token",
        namespace="MyNamespace",
        max_retries=5,
    )
    assert sender.session_token == "test_token"
    assert sender.namespace == "MyNamespace"
    assert sender.max_retries == 5
    print("✓ Initialization with optional parameters works")

    # Test validation errors
    try:
        ObsByClaraMetricsSender(
            endpoint="",
            region="eu-west-1",
            service="execute-api",
            access_key_id="key",
            secret_access_key="secret",
        )
        assert False, "Should have raised ValueError for empty endpoint"
    except ValueError as e:
        assert "endpoint is required" in str(e)
        print("✓ Validation error for empty endpoint works")


def test_cloudwatch_payload_format():
    """Test CloudWatch metrics payload format generation."""
    print("\nTesting CloudWatch payload format...")

    sender = ObsByClaraMetricsSender(
        endpoint="https://example.com",
        region="eu-west-1",
        service="execute-api",
        access_key_id="key",
        secret_access_key="secret",
    )

    # Create test data
    metric_name = "test.metric"
    test_time = datetime(2026, 1, 29, 10, 30, 0)
    values = [
        (test_time, 42.5, {"env": "prod", "host": "server1"}),
        (test_time, 37.8, {"env": "dev", "host": "server2"}),
    ]

    # Mock the HTTP request to capture the payload
    with patch('libs.metrics.requests.post') as mock_post:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        sender.send_metrics(metric_name, values)

        # Verify the request was made
        assert mock_post.called
        call_args = mock_post.call_args

        # Get the payload from the request
        payload_str = call_args.kwargs['data']
        payload = json.loads(payload_str)

        # Verify payload structure
        assert "Namespace" in payload
        assert payload["Namespace"] == "CustomMetrics"
        assert "MetricData" in payload
        assert len(payload["MetricData"]) == 2

        # Verify first metric
        metric1 = payload["MetricData"][0]
        assert metric1["MetricName"] == "test.metric"
        assert metric1["Value"] == 42.5
        assert metric1["Unit"] == "None"
        assert metric1["Timestamp"] == test_time.isoformat()
        assert len(metric1["Dimensions"]) == 2
        assert {"Name": "env", "Value": "prod"} in metric1["Dimensions"]
        assert {"Name": "host", "Value": "server1"} in metric1["Dimensions"]

        print("✓ CloudWatch payload format is correct")


def test_sigv4_signature_generation():
    """Test that SigV4 signature is correctly generated."""
    print("\nTesting SigV4 signature generation...")

    sender = ObsByClaraMetricsSender(
        endpoint="https://example.execute-api.eu-west-1.amazonaws.com/prod/metrics",
        region="eu-west-1",
        service="execute-api",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )

    with patch('libs.metrics.requests.post') as mock_post:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        test_time = datetime(2026, 1, 29, 10, 30, 0)
        values = [(test_time, 100.0, {"test": "value"})]

        sender.send_metrics("test.metric", values)

        # Verify headers contain SigV4 signature components
        call_args = mock_post.call_args
        headers = call_args.kwargs['headers']

        assert "Authorization" in headers
        assert "AWS4-HMAC-SHA256" in headers["Authorization"]
        assert "Credential=" in headers["Authorization"]
        assert "SignedHeaders=" in headers["Authorization"]
        assert "Signature=" in headers["Authorization"]
        assert "X-Amz-Date" in headers
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"

        print("✓ SigV4 signature headers are correctly generated")


def test_retry_logic():
    """Test retry logic with exponential backoff."""
    print("\nTesting retry logic...")

    sender = ObsByClaraMetricsSender(
        endpoint="https://example.com",
        region="eu-west-1",
        service="execute-api",
        access_key_id="key",
        secret_access_key="secret",
        max_retries=2,
    )

    test_time = datetime(2026, 1, 29, 10, 30, 0)
    values = [(test_time, 100.0, {"test": "value"})]

    # Test retry on 500 error
    with patch('libs.metrics.requests.post') as mock_post:
        with patch('libs.metrics.time.sleep') as mock_sleep:
            # First two calls fail with 500, third succeeds
            mock_response_fail = Mock()
            mock_response_fail.status_code = 500
            mock_response_fail.raise_for_status.side_effect = Exception("500 error")

            mock_response_success = Mock()
            mock_response_success.status_code = 200
            mock_response_success.raise_for_status = Mock()

            mock_post.side_effect = [mock_response_fail, mock_response_fail, mock_response_success]

            sender.send_metrics("test.metric", values)

            # Verify retries occurred
            assert mock_post.call_count == 3
            assert mock_sleep.call_count == 2
            # Verify exponential backoff (1s, 2s)
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)

            print("✓ Retry logic with exponential backoff works correctly")


def test_empty_values():
    """Test handling of empty values list."""
    print("\nTesting empty values handling...")

    sender = ObsByClaraMetricsSender(
        endpoint="https://example.com",
        region="eu-west-1",
        service="execute-api",
        access_key_id="key",
        secret_access_key="secret",
    )

    with patch('libs.metrics.requests.post') as mock_post:
        sender.send_metrics("test.metric", [])

        # Should not make any HTTP requests
        assert not mock_post.called
        print("✓ Empty values list is handled correctly (no request sent)")


def main():
    """Run all tests."""
    print("=" * 60)
    print("ObsByClaraMetricsSender Validation Tests")
    print("=" * 60)

    try:
        test_sign_functions()
        test_obc_init()
        test_cloudwatch_payload_format()
        test_sigv4_signature_generation()
        test_retry_logic()
        test_empty_values()

        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
