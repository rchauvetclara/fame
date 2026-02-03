# Fame - Function for Azure Monitoring Extension

This repository hosts an Azure Function App Python code in order to run Log Analytics and Resource Graph queries and
send results to ObsByClara (Prometheus-compatible endpoints), [Splunk Observability](https://www.splunk.com/en_us/observability.html) (formerly SignalFx), or
[Datadog](https://www.datadoghq.com/).


## Pre-requisites

* A Python 3.12 [Azure Function App](https://docs.microsoft.com/en-us/azure/azure-functions/functions-overview)
* A [Log Analytics Workspace](https://docs.microsoft.com/en-us/azure/azure-monitor/logs/log-analytics-overview)
with resources [Diagnostic Settings](https://docs.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings?tabs=CMD)
linked to it
* A [Table Storage](https://docs.microsoft.com/en-us/azure/storage/tables/table-storage-overview) containing the queries.
* Function [Managed Identity](https://docs.microsoft.com/en-us/azure/active-directory/managed-identities-azure-resources/overview)
    or Azure [Service Principal](https://docs.microsoft.com/en-us/azure/active-directory/develop/app-objects-and-service-principals#service-principal-object)
    with at least `Log Analytics Reader` right on the Log Analytics Workspace for Log Analytics queries, `Reader` on the
    subscription for the Resource Graph for Resource Graph queries and `Reader and Data Access` on the Storage Account if
    storage key is not provided.
* Either:
    * A Prometheus-compatible remote write endpoint (e.g., AWS Managed Prometheus, Grafana Cloud) with AWS credentials for SigV4 authentication
    * OR a [Splunk Observability](https://www.observability.splunk.com/en_us/infrastructure-monitoring.html) account and its
      associated [ingest Token](https://dev.splunk.com/observability/docs/administration/authtokens/#Organization-access-tokens)
    * OR a [Datadog](https://www.datadoghq.com/) account and its associated API key


### Variables

* **QUERIES_STORAGE_ACCOUNT_NAME** (optional): The name of the Storage Account containing the table with the queries.
If not set, use the `AzureWebJobsStorage` connection string.
* **QUERIES_STORAGE_ACCOUNT_KEY** (optional): The key to access the Storage Account containing the table with the
queries, will try to fetch it if empty. If not set, use the `AzureWebJobsStorage` connection string.
* **QUERIES_STORAGE_TABLE_NAME** (optional, defaults to `LogQueries`): The name of the table in the Storage Account
with the queries
* **METRICS_EXTRA_DIMENSIONS** (optional): Extra dimensions/tags to send along the metrics.
    Example: `env=prod,dd_monitored=true`

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

#### Other Configuration
* **LOG_ANALYTICS_WORKSPACE_GUID** (required): ID of the Log Analytics Workspace for Log Analytics queries
* **SUBSCRIPTION_ID** (required): ID of the Subscription for Resource Graph queries
* **LOG_LEVEL** (optional, defaults to `INFO`): Logging level
* **AZURE_CLIENT_ID** (optional): Azure Service Principal ID if Service Principal authentication is used
* **AZURE_TENANT_ID** (optional): Azure Tenant ID if Service Principal authentication is used
* **AZURE_CLIENT_SECRET** (optional): Azure Service Principal secret key if Service Principal authentication is used


## How it works

The function runs all the queries stored in the associated Table Storage every minute within the given
Log Analytics Workspace and sends the results to the configured metrics backend (ObsByClara, Splunk Observability, or Datadog).

Each query defines the metric value and its corresponding timestamp. All columns in the query are included as metric
dimensions, along with any extra dimensions specified through `METRICS_EXTRA_DIMENSIONS` variable. Additionally,
if available, the dimensions `fame_function_app_name`, `fame_function_app_resource_group`, and `fame_function_app_subscription_id`
are added to each metric sent.

### Metric Format

Metrics are sent to the configured backend in the native format for that backend:

- **ObsByClara/Prometheus**: Metrics are sent using Prometheus Remote Write protocol with protobuf serialization and Snappy compression. Metric names and label names are automatically sanitized to follow Prometheus naming conventions (replacing dots and hyphens with underscores, ensuring names start with a letter or underscore).

- **Datadog**: Metrics are sent using Datadog's native gauge metric format with tags.

- **Splunk Observability**: Metrics are sent using SignalFx's gauge metric format with dimensions.

All backends receive the same metric data (name, value, timestamp, dimensions), just in different wire formats.

### Table storage format

The records in the Table Storage must have the following columns:
* **MetricName**: Name of the metric to send to the configured backend (ObsByClara, Splunk Observability, or Datadog)
* **Query**: Query to run either on the Log Analytics Workspace or the Azure Resource Graph
    (See [https://docs.microsoft.com/en-us/azure/azure-monitor/logs/get-started-queries](https://docs.microsoft.com/en-us/azure/azure-monitor/logs/get-started-queries))
* **QueryType**: Type of Query to run. Can be `log_analytics` (default) or `resource_graph`.
    (See [https://docs.microsoft.com/en-us/azure/azure-monitor/logs/get-started-queries](https://docs.microsoft.com/en-us/azure/azure-monitor/logs/get-started-queries))



### Log queries requirements

The query must contain the columns `metric_value` with a metric value and `timestamp` with the datetime of the metric 
to send. The other columns must be strings and are treated as dimensions for the metric.

For Log Analytics queries, you must specify a time range in your query to avoid retrieving and sending a huge amount 
of data.

## How to deploy

You can use [Zip deployment](https://docs.microsoft.com/en-us/azure/azure-functions/deployment-zip-push),
[Azure Function Core Tools](https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local#publish)
or any other Azure deployment method to deploy this application.
