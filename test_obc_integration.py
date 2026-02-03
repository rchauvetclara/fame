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
