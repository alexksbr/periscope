# Periscope Chaos Backend

Toy FastAPI backend for generating synthetic service and LLM-style telemetry.

Run it with an ASGI server:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 uv run uvicorn chaos.app:app --reload --port 8081
```

Happy-path requests:

```bash
curl http://127.0.0.1:8081/health
curl http://127.0.0.1:8081/catalog/items
curl http://127.0.0.1:8081/inventory/peri-probe
curl -X POST http://127.0.0.1:8081/checkout \
  -H 'content-type: application/json' \
  -d '{"item_id":"probe","quantity":1,"customer_id":"demo"}'
curl -X POST http://127.0.0.1:8081/assistant/respond \
  -H 'content-type: application/json' \
  -d '{"prompt":"why did checkout slow down?"}'
```

One-off failure injection:

```bash
curl 'http://127.0.0.1:8081/catalog/items?failure=latency'
curl -H 'x-chaos-failure: http_error' http://127.0.0.1:8081/inventory/peri-probe
```

Repeatable failure injection:

```bash
curl -X PUT http://127.0.0.1:8081/admin/failures/checkout \
  -H 'content-type: application/json' \
  -d '{"kind":"dependency_error","probability":1.0,"message":"payment provider unavailable"}'

curl -X DELETE http://127.0.0.1:8081/admin/failures/checkout
```
