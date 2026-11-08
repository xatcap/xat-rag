# Orders service

## Ports and health

The orders service listens on port 8080. The health route is /health and it returns 200 when the database is reachable.

## Order lookup errors

OrderNotFoundException means that OrderService.find did not find the order id. Check that the id has no spaces or prefixes. Since release 4.2 the ids are trimmed.

## Database timeouts

A PSQLException with "connection timed out" means that the pool is full. Raise the pool size in orders.yaml or check slow queries.
