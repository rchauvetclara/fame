from dataclasses import dataclass
from datetime import datetime

import datadog
import hashlib
import hmac
import json
import logging
import os
import requests
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple

logger = logging.getLogger("metrics")


def _sign(key: bytes, msg: str) -> bytes:
    """
    Generate HMAC-SHA256 signature for AWS SigV4.

    :param key: Signing key (bytes)
    :param msg: Message to sign (string)
    :return: HMAC-SHA256 digest
    """
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region: str, service: str) -> bytes:
    """
    Derive signing key for AWS SigV4 authentication.

    :param key: AWS secret access key
    :param date_stamp: Date in YYYYMMDD format
    :param region: AWS region (e.g., 'eu-west-1')
    :param service: AWS service name (e.g., 'execute-api')
    :return: Derived signing key
    """
    kDate = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    kRegion = _sign(kDate, region)
    kService = _sign(kRegion, service)
    kSigning = _sign(kService, "aws4_request")
    return kSigning


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
    obc_namespace = os.environ.get("OBC_NAMESPACE", "CustomMetrics")
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
            "namespace": obc_namespace,
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


@dataclass
class MetricsSender(ABC):
    """
    Abstract base class defining the interface for sending metrics to different backends.
    """

    @abstractmethod
    def __init__(self):
        """
        Initialize the metrics sender with configuration.
        """
        pass

    @abstractmethod
    def send_metrics(
        self, name: str, values: List[Tuple[datetime, float, Dict[str, str]]]
    ) -> None:
        """
        Send metrics to the backend.

        :param name: Name of the metric
        :param values: List of timestamp, value and dimensions tuples
        :return: None
        """
        pass


class DatadogMetricsSender(MetricsSender):
    """
    Implementation of MetricsSender for Datadog.
    """

    def __init__(
        self,
        api_key: str,
        api_host: str = "https://api.datadoghq.eu",
    ):
        """
        Initialize the Datadog metrics sender with configuration.

        :param api_key: Datadog API key
        :param api_host: Datadog API host (optional, default: 'https://api.datadoghq.eu')
        """
        if not api_key:
            raise ValueError("Datadog API key is required")

        self.api_key = api_key
        self.api_host = api_host

        try:
            logger.debug(
                f"Initializing Datadog metrics sender with host: {self.api_host}"
            )

            # Initialize the Datadog client
            datadog.initialize(api_key=self.api_key, api_host=self.api_host)
        except Exception:
            logger.exception("Failed to initialize Datadog client")
            raise

    def send_metrics(
        self, name: str, values: List[Tuple[datetime, float, Dict[str, str]]]
    ) -> None:
        """
        Send metrics to the Datadog.

        :param name: Name of the metric
        :param values: List of timestamp, value and dimensions tuples
        :return: None
        """
        if not values:
            logger.warning(f"No metrics data to send for {name}")
            return

        # Group metrics by dimensions to minimize API calls
        metrics_by_dimensions = {}
        for dt, value, dimensions in values:
            dim_key = frozenset(dimensions.items())  # Make dimensions hashable
            if dim_key not in metrics_by_dimensions:
                metrics_by_dimensions[dim_key] = {
                    "points": [],
                    "dimensions": dimensions,
                }
            metrics_by_dimensions[dim_key]["points"].append((dt.timestamp(), value))

        # Send metrics to Datadog
        for batch in metrics_by_dimensions.values():
            logger.debug(
                f"Sending metric {name} with points {batch['points']} and dimensions {batch['dimensions']}"
            )
            try:
                datadog.api.Metric.send(
                    metric=name,
                    points=batch["points"],
                    type="gauge",
                    tags=[f"{k}:{v}" for k, v in batch["dimensions"].items()],
                )
            except Exception:
                logger.exception("Failed to send metrics to Datadog")
                raise
        logger.info(f"Sent {name} metrics to Datadog")


class SignalFxMetricsSender(MetricsSender):
    """
    Implementation of MetricsSender for SignalFx/Splunk Observability.
    """

    def __init__(self, token: str, realm: str = "eu0"):
        """
        Initialize the SignalFx metrics sender with configuration.

        :param token: SignalFx access token
        :param realm: SignalFx realm (default: 'eu0')
        """
        logger.info(f"Initializing SignalFx metrics sender with realm: {realm}")
        if not token:
            raise ValueError("SignalFx token is required")

        self.url = f"https://ingest.{realm}.signalfx.com/v2/datapoint"
        self.http_headers = {
            "Content-Type": "application/json",
            "X-SF-TOKEN": token,
        }

    def send_metrics(
        self, name: str, values: List[Tuple[datetime, float, Dict[str, str]]]
    ) -> None:
        """
        Send metrics to the SignalFx.

        :param name: Name of the metric
        :param values: List of timestamp and value tuples
        :return: None
        """
        if not values:
            logger.warning(f"No metrics data to send for {name}")
            return

        sfx_metrics = []
        for dt, v, dim in values:
            logger.debug(
                f"Sending metric {name} with value {v} at {dt} and dimensions {dim}"
            )
            sfx_metrics.append(
                {
                    "metric": name,
                    "value": v,
                    "timestamp": dt.timestamp() * 1000,
                    "dimensions": dim,
                }
            )

        try:
            res = requests.post(
                self.url,
                headers=self.http_headers,
                data=json.dumps({"gauge": sfx_metrics}),
            )
            res.raise_for_status()
        except Exception:
            logger.exception("Failed to send metrics to SignalFx")
            raise

        logger.info(f"Sent {name} metrics to SignalFx")


class ObsByClaraMetricsSender(MetricsSender):
    """
    Implementation of MetricsSender for ObsByClara using AWS SigV4 authentication.
    """

    def __init__(
        self,
        endpoint: str,
        region: str,
        service: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: str = None,
        namespace: str = "CustomMetrics",
        max_retries: int = 3,
    ):
        """
        Initialize the ObsByClara metrics sender with configuration.

        :param endpoint: Full URL of the ObsByClara API endpoint
        :param region: AWS region for SigV4 signing
        :param service: AWS service name for SigV4 signing
        :param access_key_id: AWS access key ID
        :param secret_access_key: AWS secret access key
        :param session_token: AWS session token (optional)
        :param namespace: CloudWatch namespace (default: 'CustomMetrics')
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

        self.endpoint = endpoint
        self.region = region
        self.service = service
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.session_token = session_token
        self.namespace = namespace
        self.max_retries = max_retries

    def send_metrics(
        self, name: str, values: List[Tuple[datetime, float, Dict[str, str]]]
    ) -> None:
        """
        Send metrics to ObsByClara using AWS SigV4 signed requests.

        :param name: Name of the metric
        :param values: List of timestamp, value and dimensions tuples
        :return: None
        """
        if not values:
            logger.warning(f"No metrics data to send for {name}")
            return

        # Build CloudWatch format payload
        metric_data = []
        for dt, value, dimensions in values:
            metric_datum = {
                "MetricName": name,
                "Timestamp": dt.isoformat(),
                "Value": value,
                "Unit": "None",
                "Dimensions": [
                    {"Name": k, "Value": v} for k, v in dimensions.items()
                ],
            }
            metric_data.append(metric_datum)

        payload = {"Namespace": self.namespace, "MetricData": metric_data}
        payload_json = json.dumps(payload)

        logger.debug(f"Sending payload: {payload_json}")

        # Retry logic with exponential backoff
        for attempt in range(self.max_retries + 1):
            try:
                # Sign and send the request
                response = self._send_signed_request(payload_json)
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

    def _send_signed_request(self, payload: str) -> requests.Response:
        """
        Send an AWS SigV4 signed HTTP POST request.

        :param payload: JSON payload as string
        :return: HTTP response
        """
        # Parse endpoint URL
        from urllib.parse import urlparse

        parsed_url = urlparse(self.endpoint)
        host = parsed_url.netloc
        canonical_uri = parsed_url.path if parsed_url.path else "/"

        # Request parameters
        method = "POST"
        content_type = "application/json"

        # Create timestamps
        t = datetime.utcnow()
        amz_date = t.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = t.strftime("%Y%m%d")

        # Create canonical request
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
