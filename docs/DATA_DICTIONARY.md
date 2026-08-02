# Data Dictionary

The source of truth for what every stored field means, its unit, and where it came from.
Grows every phase — updated at the end of each phase alongside `CLAUDE.md`, per the project's
"ways of working" rule. Empty in Phase 0 because no schema exists yet (that's Phase 1).

## Conventions (apply from Phase 1 onward)

- All physical quantities stored in SI units: metres, seconds, m/s, kg, °C, W, bpm. Conversion
  to imperial/display units happens at the presentation layer only, never in storage.
- Timestamps are stored as UTC, alongside the local UTC offset in seconds and the IANA
  timezone name, so both "what time was it there" and "what time is it in absolute terms" are
  always recoverable.
- Every `metric_definition` row records `first_seen_at` and `first_seen_source` — provenance
  isn't just per-value, it's per-metric-existing-at-all.

## Tables

_(populated starting Phase 1 — bronze `raw_object` and core schema)_

## Metric registry

_(populated starting Phase 1 — `metric_definition` auto-registers unknown fields; promoted
metrics get a human-readable description here)_
