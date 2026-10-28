# Clock Index Performance Baseline

**blake256r8 | Custom V1 | Auto Fan**

Measured on three 64/64 boards at the same index.
Combined GH/s covers all three boards; power is total miner wall usage.
MHz values are estimates, not measured frequencies.

| Index | Accepted GH/s per board | Combined GH/s | Estimated MHz | Average power (W) |
|---:|---:|---:|---:|---:|
| 8 | 34.72 | 104.16 | ~225 | 140.6 |
| 9 | 34.45 | 103.36 | ~224 | 146.1 |
| 10 | 35.66 | 106.99 | ~231 | 148.6 |
| 11 | 35.48 | 106.45 | ~230 | 153.2 |
| 12 | 38.08 | 114.23 | ~247 | 158.2 |
| 13 | 40.34 | 121.03 | ~262 | 165.4 |
| 14 | 41.17 | 123.51 | ~267 | 169.9 |
| 15 | 42.87 | 128.62 | ~278 | 174.4 |
| 16 | 44.12 | 132.36 | ~286 | 178.5 |
| 17 | 44.41 | 133.24 | ~288 | 184.3 |
| 18 | 47.06 | 141.17 | ~305 | 189.6 |
| 19 | 48.21 | 144.64 | ~313 | 193.5 |
| 20 | 50.68 | 152.05 | ~329 | 198.4 |
| 21 | 48.94 | 146.82 | ~318 | 203.5 |
| 22 | 51.24 | 153.73 | ~333 | 208.5 |
| 23 | 51.71 | 155.13 | ~336 | 215.1 |
| 24 | 54.98 | 164.94 | ~357 | 221.2 |
| 25 | 54.57 | 163.70 | ~354 | 227.0 |
| 26 | 56.94 | 170.81 | ~370 | 231.0 |
| 27 | 58.47 | 175.40 | ~379 | 236.5 |
| 28 | 60.61 | 181.84 | ~393 | 241.9 |
| 29 | 59.41 | 178.22 | ~386 | 246.7 |
| 30 | 61.64 | 184.91 | 400 | 251.5 |
| 31 | 61.49 | 184.48 | ~399 | 256.0 |
| 32 | 63.72 | 191.15 | ~413 | 259.3 |

Source: [clock-baseline.json](overlay/opt/scripta/etc/clock-baseline.json).
Method: [technical guide](TECHNICAL-GUIDE.md#algorithm-specific-baseline-data).
