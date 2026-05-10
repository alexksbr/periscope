CREATE DATABASE IF NOT EXISTS periscope;

CREATE TABLE IF NOT EXISTS periscope.spans
(
    trace_id FixedString(32),
    span_id FixedString(16),
    parent_span_id Nullable(FixedString(16)),
    tenant_id LowCardinality(String) DEFAULT 'default',
    start_time DateTime64(9, 'UTC'),
    end_time DateTime64(9, 'UTC'),
    duration_ns UInt64,
    name LowCardinality(String),
    span_kind Enum8(
        'Unspecified' = 0,
        'Internal' = 1,
        'Server' = 2,
        'Client' = 3,
        'Producer' = 4,
        'Consumer' = 5
    ),
    status_code Enum8(
        'Unset' = 0,
        'Ok' = 1,
        'Error' = 2
    ),
    status_message Nullable(String),
    service_name LowCardinality(String),
    service_version LowCardinality(Nullable(String)),
    deployment_environment LowCardinality(Nullable(String)),
    http_method LowCardinality(Nullable(String)),
    http_route LowCardinality(Nullable(String)),
    http_status_code Nullable(UInt16),
    db_system LowCardinality(Nullable(String)),
    db_operation LowCardinality(Nullable(String)),
    messaging_system LowCardinality(Nullable(String)),
    messaging_destination LowCardinality(Nullable(String)),
    error_type LowCardinality(Nullable(String)),
    gen_ai_system LowCardinality(Nullable(String)),
    gen_ai_request_model LowCardinality(Nullable(String)),
    gen_ai_input_tokens Nullable(UInt32),
    gen_ai_output_tokens Nullable(UInt32),
    user_id Nullable(String),
    attributes Map(LowCardinality(String), String),
    resource_attributes Map(LowCardinality(String), String),
    events Nested(
        name LowCardinality(String),
        timestamp DateTime64(9, 'UTC'),
        attributes Map(LowCardinality(String), String)
    ),
    links Nested(
        trace_id FixedString(32),
        span_id FixedString(16),
        attributes Map(LowCardinality(String), String)
    )
)
ENGINE = MergeTree
PARTITION BY toDate(start_time)
ORDER BY (tenant_id, start_time, service_name, trace_id)
TTL toDateTime(start_time) + INTERVAL 30 DAY DELETE;
