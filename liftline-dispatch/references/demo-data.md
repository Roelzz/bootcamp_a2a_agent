# LiftLine synthetic service fixture

Fixture ID: LIFTLINE-DEMO-v1

All services, capacity, coverage, slots, and prices are fictional workshop
data. They are not real offers, safety assessments, or delivery commitments.

## Coverage

Supported origin:
Zava Regional depot, also identified as ZAVA-DEPOT.

Supported destination:
Riverside renovation site.

There are no other routes in this fixture.

## Arrival window

Both services offer the fictional arrival window:

08:00-09:30 on the explicitly requested demo calendar day, in the explicitly
supplied timezone.

Treat the slot as available for an otherwise eligible simulation request.
Do not infer the requested day or timezone from the server clock.
The service cannot guarantee a deadline earlier than 09:30.
There are no alternative or earlier slots.

## Service sheet

| Service ID | Service | Delivery charge | Loaded capacity | Site forklift required |
| --- | --- | --- | --- | --- |
| STANDARD | Standard delivery | EUR 120 | 1,500 kg | Yes |
| TAIL-LIFT | Tail-lift delivery | EUR 180 | 1,500 kg | No |

These are final demo delivery charges. Do not add unprovided taxes or
surcharges.

An explicitly labelled, nonnegative EUR demo quote override may replace the
price for a named service for the current evaluation only. No other fixture
facts can be overridden.

## Baseline shipment

Product: repair mortar, MORTAR-20.
Quantity: 60 whole bags.
Unit net weight: 20 kg.

Product weight:
60 x 20 kg = 1,200 kg.

Fixed pallet/packaging allowance for this baseline shipment:
100 kg.

Loaded shipment weight:
1,300 kg.

## Baseline handling assumptions

For this fictional baseline shipment, assume:
- One compatible pallet.
- Suitable vehicle access.
- Level hardstanding.
- Adequate tail-lift rating.
- A carrier-provided pallet truck and operator.
- Ground-level unloading.

Disclose these as assumptions, not verified site facts.

The baseline customer request says there is no forklift on site. For other
requests, use the caller's stated forklift availability.

If the caller reports stairs, unsuitable access, or incompatible handling, do
not claim the baseline assumptions resolve the problem.

For another quantity or configuration, do not automatically reuse the 100 kg
packaging allowance. Require explicit loaded weight and appropriate handling
information.

If product weight is known, loaded weight cannot be less than product weight.

## Responsibility

LiftLine owns service eligibility and quotes only.
Zava independently owns stock availability and internal approval rules.
Do not request Zava stock tables or approval thresholds to quote delivery.
Nothing is reserved or booked.
