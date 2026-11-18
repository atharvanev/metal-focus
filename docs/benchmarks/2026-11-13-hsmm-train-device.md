# HSMM training device benchmark (2026-11-13)

- Machine: Apple M2 Pro, macOS 26.5.2; MLX 0.32.4
- Synthetic days from `hsmm.simulate.default_true_params`, T = 720 windows/day; median of 5 runs

| workload | device | days | median | min | per |
|---|---|---|---|---|---|
| nightly_update | cpu | 7 | 0.16 s | 0.15 s | call |
| fit_em | cpu | 7 | 0.16 s | 0.16 s | iteration |
| nightly_update | gpu | 7 | 0.45 s | 0.45 s | call |
| fit_em | gpu | 7 | 0.45 s | 0.45 s | iteration |
| nightly_update | cpu | 14 | 0.16 s | 0.16 s | call |
| fit_em | cpu | 14 | 0.16 s | 0.16 s | iteration |
| nightly_update | gpu | 14 | 0.45 s | 0.44 s | call |
| fit_em | gpu | 14 | 0.43 s | 0.43 s | iteration |
| nightly_update | cpu | 28 | 0.15 s | 0.15 s | call |
| fit_em | cpu | 28 | 0.16 s | 0.15 s | iteration |
| nightly_update | gpu | 28 | 0.45 s | 0.45 s | call |
| fit_em | gpu | 28 | 0.43 s | 0.43 s | iteration |
| nightly_update | cpu | 56 | 0.17 s | 0.17 s | call |
| fit_em | cpu | 56 | 0.17 s | 0.17 s | iteration |
| nightly_update | gpu | 56 | 0.46 s | 0.46 s | call |
| fit_em | gpu | 56 | 0.46 s | 0.46 s | iteration |
| live_step | cpu | - | 0.082 ms | 0.081 ms | step |
| live_step | gpu | - | 0.266 ms | 0.265 ms | step |

GPU speedup (CPU median / GPU median; >1 means GPU is faster):

- nightly_update (7 days): 0.35x
- fit_em (7 days): 0.35x
- nightly_update (14 days): 0.35x
- fit_em (14 days): 0.38x
- nightly_update (28 days): 0.34x
- fit_em (28 days): 0.37x
- nightly_update (56 days): 0.37x
- fit_em (56 days): 0.38x
- live_step: 0.31x

Largest |param_cpu - param_gpu| after one nightly_update:

- 7 days: 8.94e-07
- 14 days: 1.40e-06
- 28 days: 2.65e-06
- 56 days: 6.20e-06

## Conclusion

**Keep `TRAIN_DEVICE = mx.cpu`.** The GPU is about 2.7x slower on every workload, including 56 days, which is twice the training window.

- Time is flat in the number of days on both devices (CPU ~0.16 s, GPU ~0.45 s per nightly update). The cost is the 2 x T sequential recursion steps: `forward` and `smooth` call `mx.eval` once per window. Batching more days only widens arrays that are already tiny (N x 4 x 30). Per-step dispatch and sync on the GPU (~0.3 ms vs ~0.1 ms on CPU) dominates.
- CPU and GPU agree to ~1e-6 (float32), so the choice affects speed only.
- A nightly update takes 0.16 s on CPU, so there is nothing worth optimizing.
- Re-run when the training shape changes, e.g. GRU training over many sequences: `uv run python -m hsmm.bench --days 7 14 28 --out docs/benchmarks/<date>-....md`.
