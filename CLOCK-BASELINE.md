# Clock Index Performance Baseline

Algorithm: **blake256r8**. These are shared lab examples from completed V1
trials, not live readings or guarantees for another board.

GH/s is the pooled accepted-work rate per board from contributing completed
windows. MHz is a **performance-equivalent estimate, not measured ASIC
frequency**. Index 30 uses the nominal **400 MHz** anchor.

| Index | Accepted GH/s per board | Estimated MHz |
|---:|---:|---:|
| 8 | 32.12 | ~210 |
| 9 | 36.70 | ~242 |
| 10 | 36.85 | ~238 |
| 11 | 36.78 | ~240 |
| 12 | 36.62 | ~242 |
| 13 | 36.31 | ~235 |
| 14 | 37.84 | ~247 |
| 15 | 40.82 | ~270 |
| 16 | 41.28 | ~267 |
| 17 | 44.63 | ~292 |
| 18 | 47.07 | ~311 |
| 19 | 48.60 | ~314 |
| 20 | 47.53 | ~311 |
| 21 | 46.22 | ~305 |
| 22 | 48.74 | ~315 |
| 23 | 53.01 | ~346 |
| 24 | 54.17 | ~358 |
| 25 | 54.93 | ~355 |
| 26 | 60.35 | ~394 |
| 27 | 62.40 | ~412 |
| 28 | 62.40 | ~403 |
| 29 | 62.02 | ~405 |
| 30 | 61.22 | 400 |
| 31 | 62.79 | ~406 |
| 32 | 64.79 | ~419 |

Global estimated GH/s is three times the shared per-board value. That is an
extrapolation, not a measured all-board result. Nearby points can vary with
share-count noise, cooling, and work delivery; do not interpolate or invert this
table into a calibrated MHz control.

Each index, including Index 31 and Index 32, has at least one completed thirty-minute
observation. This is limited screening, not qualification of every board,
long-term stability, or physical MHz. Values are scoped
to the recorded host and V1 firmware identities and must not be reused for
another algorithm or treated as stock-firmware measurements.

Source: [clock-baseline.json](overlay/opt/scripta/etc/clock-baseline.json).
That file retains measured board IDs, durations, accepted shares, error counts,
and operating tuples. Calculation and test limits are in the
[technical guide](TECHNICAL-GUIDE.md#algorithm-specific-baseline-data).
