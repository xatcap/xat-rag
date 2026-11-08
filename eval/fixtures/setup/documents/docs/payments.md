# Payments service

## Card declines

A PaymentDeclinedException with code E402 is a decline from the card provider. The service does not retry declines.

## Refunds

Refunds run in a nightly batch job named refund-runner. A failed refund is logged with RefundFailedError and the refund id.
