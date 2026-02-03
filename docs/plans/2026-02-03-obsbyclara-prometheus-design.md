# ObsByClara Prometheus Remote Write Design

**Date**: 2026-02-03
**Status**: Approved

## Overview

Update the ObsByClara metric sender to use Prometheus Remote Write protocol with protobuf format instead of CloudWatch format, while preserving the existing AWS SigV4 authentication mechanism. This enables Fame to send metrics to AWS Managed Prometheus (AMP).

## Goals

- Replace CloudWatch payload format with Prometheus Remote Write protobuf
- Preserve all existing SigV4 authentication logic
- Maintain retry logic and error handling
- Minimize external dependencies by vendoring protobuf definitions

## Architecture Impact

### Changes
- **ObsByClaraMetricsSender class**: Payload format and content-type only
- **Dependencies**: Add vendored protobuf definitions and python-snappy

### Preserved
- MetricsSender interface unchanged
- SigV4 signing logic unchanged
- Retry logic with exponential backoff unchanged
- Factory function pattern unchanged

## Technical Implementation

### Prometheus Remote Write Protocol

**Format**:
- Uses `prometheus.WriteRequest` protobuf message
- Contains `TimeSeries` with labels and samples
- Payload is Snappy-compressed before sending
- Content-Type: `application/x-protobuf`
- Endpoint: typically `/api/v1/remote_write` appended to base URL

### Metric Conversion Logic

**Metric Name Transformation**:
```
Input:  azure.vm.cpu_usage
Output: azure_vm_cpu_usage

Rules:
- Convert dots to underscores
- Remove invalid characters (keep only [a-zA-Z0-9_:])
- Ensure starts with letter or underscore
- Log warnings for modified names
```

**Labels (Dimensions)**:
- Fame dimensions become Prometheus labels directly
- Label names follow same sanitization rules
- Special `__name__` label holds the metric name

**Example**:
```
Fame Input:
  name: "azure.vm.cpu_usage"
  value: 75.5
  timestamp: 2024-01-15T10:30:00Z
  dimensions: {
    "resource_group": "rg1",
    "vm_name": "vm-01",
    "region": "westeurope"
  }

Prometheus Output:
  TimeSeries {
    labels: [
      {name: "__name__", value: "azure_vm_cpu_usage"},
      {name: "resource_group", value: "rg1"},
      {name: "vm_name", value: "vm-01"},
      {name: "region", value: "westeurope"}
    ]
    samples: [
      {value: 75.5, timestamp: 1705316400000}
    ]
  }
```

### Implementation Changes

#### ObsByClaraMetricsSender.__init__
**Removed parameters**:
- `namespace` - not applicable for Prometheus

**Unchanged parameters**:
- `endpoint` - base URL
- `region` - AWS region for SigV4
- `service` - typically `aps` for AWS Managed Prometheus
- `access_key_id`, `secret_access_key`, `session_token`
- `max_retries`

**Logic changes**:
- Validate endpoint format
- Optionally append `/api/v1/remote_write` if not present

#### ObsByClaraMetricsSender.send_metrics
**Changes**:
1. Build Prometheus `WriteRequest` with multiple `TimeSeries`
2. Serialize protobuf to bytes
3. Snappy-compress the bytes
4. Pass compressed bytes to `_send_signed_request`

**Preserved**:
- Retry logic with exponential backoff
- Error handling for timeout, connection, HTTP errors
- Logging at each step

#### ObsByClaraMetricsSender._send_signed_request
**Changes**:
- Content-Type: `application/x-protobuf` (was `application/json`)
- Payload is bytes (was JSON string)

**Preserved**:
- All SigV4 signing logic
- Header construction
- Request sending

### Dependencies

**New packages**:
- `python-snappy` - for Snappy compression
- Vendored protobuf definitions for Prometheus Remote Write (minimal `.proto` files)

**Rationale for vendoring**:
- Avoid large dependencies like full `prometheus-client`
- Only need Remote Write message definitions
- Reduces package size and dependencies

### Environment Variables

**Removed**:
- `OBC_NAMESPACE` - not used in Prometheus

**Unchanged**:
- `OBC_ENDPOINT` - base URL (e.g., `https://aps-workspaces.eu-west-1.amazonaws.com/workspaces/ws-xxx`)
- `OBC_REGION` - AWS region for SigV4
- `OBC_SERVICE` - typically `aps`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
- `OBC_MAX_RETRIES`

**Updated factory function**:
- Remove `obc_namespace` from factory
- Update error message to remove namespace requirement

## Error Handling

### Preserved Behavior
- Retry on timeout, connection errors
- Retry on 5xx and 429 (rate limiting)
- Exponential backoff (2^attempt seconds)
- Detailed debug logging

### New Error Scenarios
1. **Protobuf serialization failure**: Log error and raise exception
2. **Snappy compression failure**: Log error and raise exception
3. **Invalid metric/label names**: Sanitize with warning logs

### Metric Name Sanitization

Helper function to sanitize names:
```python
def _sanitize_metric_name(name: str) -> str:
    """
    Sanitize metric name to follow Prometheus naming conventions.
    - Replace invalid characters with underscores
    - Ensure starts with letter or underscore
    - Log warnings for modifications
    """
```

Examples:
- `azure.cpu-usage%` → `azure_cpu_usage_` (with warning)
- `123_metric` → `_123_metric` (with warning)

## Testing Strategy

### Unit Tests
1. Metric name sanitization (dots, hyphens, special chars, leading digits)
2. Protobuf message construction
3. Multiple metrics with same dimensions (grouping)
4. Multiple metrics with different dimensions
5. Timestamp conversion (datetime to milliseconds since epoch)
6. Snappy compression correctness
7. SigV4 signing with protobuf content-type
8. Empty values list handling

### Integration Tests
- Local Prometheus with remote write enabled (optional)
- AWS Managed Prometheus workspace (end-to-end)

## Migration Path

### For Existing Users on feat/obc Branch
1. Update environment variables: Remove `OBC_NAMESPACE` if set
2. Verify `OBC_SERVICE=aps` for AWS Managed Prometheus
3. Ensure endpoint is AMP workspace URL
4. Redeploy function

### Breaking Changes
- `OBC_NAMESPACE` environment variable no longer used
- Payload format completely different (users must update backend from CloudWatch-compatible to Prometheus)

## Files to Modify

1. `src/libs/metrics.py`:
   - Update `ObsByClaraMetricsSender` class
   - Update factory function `get_metrics_sender()`
   - Add protobuf message builder helper
   - Add metric name sanitization helper

2. `requirements.txt` or `pyproject.toml`:
   - Add `python-snappy`

3. `src/libs/prometheus_remote_write.proto` (new vendored file):
   - Minimal protobuf definitions for Remote Write

4. `README.md`:
   - Update ObsByClara configuration documentation
   - Remove namespace reference
   - Add Prometheus-specific notes

## Success Criteria

- ObsByClara sends metrics in Prometheus Remote Write format
- SigV4 authentication works with protobuf payload
- Metrics appear correctly in AWS Managed Prometheus
- All existing retry and error handling preserved
- Unit tests pass
- No breaking changes to other metric senders (Datadog, SignalFx)
