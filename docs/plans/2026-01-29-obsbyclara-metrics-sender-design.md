# ObsByClara MetricsSender Implementation Design

**Date:** 2026-01-29
**Status:** Approved

## Overview

This document describes the design for implementing a third MetricsSender implementation called `ObsByClaraMetricsSender` that will send metrics to the ObsByClara observability platform using AWS SigV4 authentication.

## Background

The codebase currently supports two metrics backends:
- **DatadogMetricsSender**: Sends metrics to Datadog
- **SignalFxMetricsSender**: Sends metrics to SignalFx/Splunk Observability

ObsByClara requires AWS SigV4 request signing for authentication, which differs from the token-based authentication used by the existing implementations.

## Architecture

### Class Structure

`ObsByClaraMetricsSender` will:
- Inherit from the abstract `MetricsSender` base class
- Implement the required `send_metrics()` method
- Follow the same pattern as existing implementations

### Configuration

The implementation will use the following environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OBC_ENDPOINT` | Yes | - | Full URL of the ObsByClara API endpoint |
| `OBC_REGION` | Yes | - | AWS region for SigV4 signing |
| `OBC_SERVICE` | Yes | - | AWS service name for SigV4 signing |
| `AWS_ACCESS_KEY_ID` | Yes | - | AWS access key (standard AWS credential) |
| `AWS_SECRET_ACCESS_KEY` | Yes | - | AWS secret key (standard AWS credential) |
| `AWS_SESSION_TOKEN` | No | - | AWS session token (for temporary credentials) |
| `OBC_NAMESPACE` | No | `CustomMetrics` | CloudWatch namespace for metrics |
| `OBC_MAX_RETRIES` | No | `3` | Maximum number of retry attempts |

### Factory Integration

The `get_metrics_sender()` factory function will be updated with the following priority order:
1. Datadog (if `DD_API_KEY` is present)
2. SignalFx (if `SFX_TOKEN` is present)
3. ObsByClara (if `OBC_ENDPOINT` is present)
4. Raise error if no configuration found

## AWS SigV4 Signature Implementation

### Signature Functions

Two utility functions will be added to support SigV4 signing:

```python
def _sign(key: bytes, msg: str) -> bytes:
    """Generate HMAC-SHA256 signature"""
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def _get_signature_key(key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derive signing key for AWS SigV4"""
    kDate = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    kRegion = _sign(kDate, region)
    kService = _sign(kRegion, service)
    kSigning = _sign(kService, "aws4_request")
    return kSigning
```

These functions are based on the AWS SigV4 signing example from:
https://github.com/aws-samples/sigv4-signing-examples/blob/main/no-sdk/python/main.py

### Signing Process

For each HTTP request, the following steps will be performed:

1. **Construct payload**: Build JSON payload in CloudWatch Metrics format
2. **Hash payload**: Calculate SHA256 hash of the payload
3. **Build canonical request**: Combine HTTP method, URI, headers, and payload hash
4. **Create string to sign**: Include algorithm, timestamp, credential scope, and request hash
5. **Derive signing key**: Use `_get_signature_key()` with secret key, date, region, and service
6. **Calculate signature**: HMAC-SHA256 of string to sign with signing key
7. **Build Authorization header**: Assemble header with credentials, signed headers, and signature
8. **Send request**: POST with all required headers

## Data Format

### CloudWatch Metrics Format

The payload will follow AWS CloudWatch PutMetricData format:

```json
{
  "Namespace": "CustomMetrics",
  "MetricData": [
    {
      "MetricName": "metric_name",
      "Timestamp": "2026-01-29T10:30:00Z",
      "Value": 123.45,
      "Unit": "None",
      "Dimensions": [
        {"Name": "dimension_key", "Value": "dimension_value"}
      ]
    }
  ]
}
```

### Data Transformation

The `send_metrics()` method receives:
```python
name: str
values: List[Tuple[datetime, float, Dict[str, str]]]
```

Each tuple will be transformed to a MetricData object:
- `datetime` → `Timestamp` (ISO 8601 format)
- `float` → `Value`
- `Dict[str, str]` → `Dimensions` array of `{"Name": key, "Value": value}`
- `name` → `MetricName`

## Error Handling and Retry

### Retry Strategy

The implementation will include exponential backoff retry logic:

- **Max retries**: Configurable via `OBC_MAX_RETRIES` (default: 3)
- **Backoff delays**: 1s, 2s, 4s (exponential: 2^attempt seconds)
- **Total attempts**: max_retries + 1 (initial attempt + retries)

### Retriable vs Non-Retriable Errors

**Retriable errors** (will retry):
- Network timeouts (`requests.exceptions.Timeout`)
- Connection errors (`requests.exceptions.ConnectionError`)
- HTTP 5xx server errors
- HTTP 429 (throttling/rate limiting)

**Non-retriable errors** (immediate failure):
- HTTP 4xx client errors (except 429)
- Invalid credentials
- Malformed requests

### Implementation Pattern

```python
max_retries = int(os.environ.get("OBC_MAX_RETRIES", "3"))

for attempt in range(max_retries + 1):
    try:
        response = requests.post(url, headers=headers, data=payload)
        response.raise_for_status()
        logger.info(f"Successfully sent {name} metrics to ObsByClara")
        break
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        if attempt < max_retries:
            delay = 2 ** attempt
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)
            continue
        logger.error(f"Failed after {max_retries + 1} attempts")
        raise
    except requests.exceptions.HTTPError as e:
        if e.response.status_code >= 500 or e.response.status_code == 429:
            if attempt < max_retries:
                delay = 2 ** attempt
                logger.warning(f"HTTP {e.response.status_code} error. Retrying in {delay}s...")
                time.sleep(delay)
                continue
        logger.error(f"HTTP error: {e}")
        raise
```

### Logging

Comprehensive logging at each stage:
- **DEBUG**: Signature generation details, payload construction
- **INFO**: Successful metric transmission, retry attempts
- **WARNING**: Retriable errors, retry delays
- **ERROR**: Final failures after all retries exhausted
- **EXCEPTION**: Unexpected errors with stack traces

## Dependencies

New imports required:
```python
import hashlib
import hmac
import time
```

Existing dependencies are sufficient:
- `requests` (already used by SignalFxMetricsSender)
- `json` (already imported)
- `datetime` (already imported)

## Testing Considerations

The implementation should be tested with:
1. Valid credentials and successful metric submission
2. Invalid credentials (authentication failure)
3. Network timeouts (retry behavior)
4. Server errors 5xx (retry behavior)
5. Client errors 4xx (no retry)
6. Rate limiting 429 (retry behavior)
7. Multiple metrics with various dimensions
8. Empty metrics list (should log warning and return)

## Future Enhancements

Potential improvements for later iterations:
- Configurable CloudWatch Unit types (currently hardcoded to "None")
- Batch size limits to avoid oversized payloads
- Custom namespace per metric
- Async request sending for better performance
- Metrics buffering and periodic flushing

## Implementation Checklist

- [ ] Add SigV4 signing utility functions (`_sign`, `_get_signature_key`)
- [ ] Implement `ObsByClaraMetricsSender` class
- [ ] Add `__init__()` with configuration validation
- [ ] Implement `send_metrics()` with CloudWatch format transformation
- [ ] Add AWS SigV4 signing logic to each request
- [ ] Implement retry logic with exponential backoff
- [ ] Update `get_metrics_sender()` factory function
- [ ] Add comprehensive logging
- [ ] Add unit tests
- [ ] Update documentation/README with ObsByClara configuration
