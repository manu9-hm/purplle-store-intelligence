# Camera Mapping

## CAM1

Major Coverage:

* Skincare Wall (EB, TFS, GV, DermDoc, Minimalist, Aqualogica, Pilgrim, D&K)

Partial Coverage:

* Fragrance Unit
* Nails Section
* Center Store Area

Purpose:

* Monitor skincare browsing
* Track customer dwell time

---

## CAM2

Major Coverage:

* Makeup Wall (Maybelline, Faces, Lakme, Swiss Beauty, Mars, NyBae, Alps, L'Oreal)

Partial Coverage:

* Fragrance Unit
* Nails Section
* Mirror Area
* Center Store Area

Purpose:

* Monitor makeup browsing
* Track customer dwell time

Calibration Workflow:

1. Open a representative CAM2 frame in the zone calibrator.
2. Draw `makeup_zone` over the customer standing/walking area immediately in front of the Makeup Wall.
3. Keep the polygon on the floor area where a shopper's foot point lands, not on the wall shelves or product fixtures.
4. Include the visible browsing strip for Maybelline, Faces, Lakme, Swiss Beauty, Mars, NyBae, Alps, and L'Oreal.
5. Avoid the mirror area, center-store walkway, fragrance unit, and nails section unless the business question explicitly needs those areas.
6. Save the result as `configs/zones/cam2_zones.json`.

Recommended `makeup_zone`:

* Use a single `browsing` zone named `makeup_zone`.
* Draw the polygon along the floor-facing customer area in front of the makeup wall.
* The lower polygon edge should sit near the aisle side where shoppers stand.
* The upper polygon edge should stop before the vertical wall/shelf area, because detections use the bottom-center foot point.

---

## CAM3

Major Coverage:

* Entrance Area

Purpose:

* Footfall monitoring
* Entry/Exit estimation

Calibration Workflow:

1. Open a representative CAM3 frame in the zone calibrator.
2. Draw `entrance_zone` as a narrow floor polygon that straddles the doorway threshold.
3. Include enough depth on both sides of the threshold so a tracked foot point is inside the polygon while crossing.
4. Do not cover the whole entrance camera view. A smaller threshold band reduces false ENTRY and EXIT events from people lingering nearby.
5. Save the result as `configs/zones/cam3_zones.json`.

ENTRY / EXIT / REENTRY Workflow:

* `ENTRY` is emitted when a tracked foot point crosses the middle of `entrance_zone` in the configured entry direction.
* `EXIT` is emitted when a tracked foot point crosses the same threshold in the opposite direction.
* `REENTRY` is emitted when the same track previously emitted `EXIT` and later crosses back in the entry direction.
* The default entry direction is `down`, meaning movement from smaller y-values to larger y-values in image coordinates counts as entering the store.
* If CAM3 is mounted so entering customers move upward in the image, run event generation with `--entry-direction up`.
* Tracker-loss exits are disabled by default; use directional threshold crossing for footfall quality.

---

## CAM4

Major Coverage:

* Storage Room

Purpose:

* Staff and inventory activity monitoring

---

## CAM5

Major Coverage:

* Billing Counter

Purpose:

* Checkout activity monitoring
* Conversion analysis

Calibration Workflow:

1. Open a representative CAM5 frame in the zone calibrator.
2. Draw `billing_zone` on the customer-side floor area immediately in front of and beside the billing counter.
3. Keep the polygon on the floor where customer foot points land. Do not draw over the counter surface, POS machine, staff chair, or cashier side of the desk.
4. Include the standing/queue area between the customer-facing counter edge and the right-side display wall.
5. Exclude the product shelf walkway at the top-left unless customers clearly queue there in the footage.
6. Save the result as `configs/zones/cam5_zones.json`.

Recommended `billing_zone`:

* Use a single zone named `billing_zone` with `zone_type` set to `billing`.
* The polygon should cover the customer waiting/checkout floor area, not the employee workspace.
* `BILLING_QUEUE_JOIN` is emitted when a tracked visitor enters `billing_zone` and the active `queue_depth` in that zone is greater than 1.
* `metadata.queue_depth` records the number of currently tracked visitors inside `billing_zone` at the join moment.
* `BILLING_QUEUE_ABANDON` is intentionally not implemented yet because the available POS data does not establish a reliable visitor-to-transaction match.
