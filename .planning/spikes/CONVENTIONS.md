# Spike Conventions

Patterns and stack choices established across spike sessions. New spikes follow these unless the question requires otherwise.

## Stack

- Keep the existing Flask application unchanged while evaluating the separate R data layer.
- Pin `baseballr` releases and browser versions explicitly so NCAA behavior can be reproduced.
- Use local Docker for R/browser experiments because the intended operating model is free, always available, and has no hosted cold start.

## Structure

- Store experiments under `.planning/spikes/NNN-name/`.
- Keep raw live-run output under `results/` and commit only small, sanitized evidence summaries.

## Patterns

- Exercise exported package functions first.
- When an exported function swallows the cause, use package-internal requests only for diagnosis and label compatibility patches explicitly.
- Run NCAA requests sequentially and preserve `baseballr`'s courtesy delay.
- Record row counts, columns, duration, messages, errors, runtime versions, image size, and observed memory.

## Tools & Libraries

- `baseballr` 2.0.0 was the pinned package for Spike 001 but its live NCAA roster/stats path was invalidated.
- `chromote` 0.5.1 with Google Chrome 156 was tested; it did not clear the current NCAA Akamai interstitial.
