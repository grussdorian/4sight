# Buffer Inventory Policy

The buffer warehouse holds safety stock of critical wafers and substrates,
expressed as a percentage of target.

- Target stock is 70% or higher under normal operations.
- Stock below 30% triggers a replenishment alert (medium severity).
- Stock below 25% is high severity; below 15% is critical and risks a line stop.
- Buffer stock is the primary shock absorber for upstream supply or yield
  disruption; a healthy buffer mitigates downstream impact.
