# Shipping & Fulfillment

Tracking itself (`Shipment`, `ShipmentStatusHistory`, `PincodeServiceability`,
carrier/tracking-number/tracking-URL fields, the buyer-facing order
tracking view, and shipment-status emails) has existed since the initial
build. What was missing — until this Delhivery integration — was any live
connection to a real courier: everything was staff pasting a tracking
number in by hand after shipping a package themselves.

## What this integration does

- **Automatic waybill (AWB) creation** — a staff admin action manifests an
  order with Delhivery and records the returned waybill as the shipment's
  tracking number/carrier.
- **Automatic status sync** — a Celery Beat task polls Delhivery every 30
  minutes for every non-terminal Delhivery shipment and updates its status
  (in transit, out for delivery, delivered, NDR, RTO) — see
  `fulfillment/tasks.py`.
- **NDR (failed delivery) notification** — when a sync detects a "delivery
  attempt failed" status, the buyer gets an email automatically (see
  `templates/emails/shipment_ndr.*`).
- **RTO** — mapped onto the existing `Shipment.Status.RETURNED` ("Returned
  to seller"); no separate status was added since that's the same
  end-state a manually-recorded return already used.

## What this does *not* do

- **Shipping label PDFs** — `fulfillment/couriers/delhivery.py` has a
  `fetch_label_pdf()` function, but nothing in the admin surfaces it yet
  (no download button/action). Wire that up once waybill creation itself
  is confirmed working end-to-end against a live account.
- **A second courier/aggregator** — this is a direct Delhivery
  integration, not a multi-carrier abstraction. `Shipment.Carrier` already
  lists Blue Dart/DTDC/India Post/Ekart/self-shipped/other as options for
  staff to record manually; only Delhivery has an API integration behind it.
- **Per-product weight** — every shipment is manifested with a flat
  `DELHIVERY_DEFAULT_PACKAGE_WEIGHT_GRAMS` (default 500g), since products
  don't currently carry their own weight. Fine for small/light goods;
  revisit if the catalog grows meaningfully heavier items.

## ⚠️ This needs live verification before going live

`fulfillment/couriers/delhivery.py` was written against Delhivery's
publicly documented B2C REST API shape — **without a live Delhivery
account to test against**, because API access hadn't been provisioned yet
when this was built. Courier APIs do change. Before relying on this in
production:

1. Sign up for Delhivery API access and get `DELHIVERY_API_TOKEN` plus
   your registered pickup location's exact name (`DELHIVERY_PICKUP_LOCATION_NAME`).
2. Set both (plus, ideally, `DELHIVERY_BASE_URL` pointed at Delhivery's
   staging environment if they offer one) in your local `.env`.
3. Run through one real waybill creation → tracking sync cycle manually
   (via `python manage.py shell` calling `fulfillment.services.assign_delhivery_waybill`
   / `sync_shipment_tracking` directly against a test order, or via the
   admin actions on a real `Shipment`) and confirm the response shapes in
   `fulfillment/couriers/delhivery.py` (`packages[0]["waybill"]`,
   `ShipmentData[0]["Shipment"]["Status"]["Status"]`, the status strings
   in `_STATUS_MAP`) actually match what Delhivery returns. Adjust
   whatever doesn't.
4. Only then point `DELHIVERY_BASE_URL` at production and go live.

Until `DELHIVERY_API_TOKEN` is set, every Delhivery code path (the admin
actions, the periodic sync task) either no-ops or raises a clear "not
configured" error — nothing here activates by accident.

## Staff workflow, once live

1. Payment captures → `Shipment` created automatically, `PENDING`, no
   carrier assigned yet (unchanged from before).
2. Staff select the shipment(s) in the Django admin and run the "Assign
   Delhivery waybill (AWB)" action — this calls Delhivery's API and fills
   in `carrier`, `tracking_number`, `tracking_url`.
3. From here, `sync-delhivery-shipment-tracking` (Celery Beat, every 30
   min) keeps `status` current automatically. Staff can also run "Sync
   tracking status from Delhivery" on-demand from the admin for an
   immediate check.
4. An NDR triggers an automatic buyer email; RTO/delivered/etc. flow
   through the same `order_status_changed` email the buyer already gets
   for any order status change (see `fulfillment/signals.py`).

Non-Delhivery shipments (self-shipped, or another carrier staff record by
hand) are entirely unaffected — the sync task only ever touches shipments
with `carrier=Shipment.Carrier.DELHIVERY`.
