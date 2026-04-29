# Replay Findings

This note documents a one-time validation experiment used to test whether
bounded `gp_history` queries can support the archive's rolling daily snapshot
model.

The Jan 1-to-current validation run confirmed the API strategy, but also set an
important boundary on what historical reconstruction can prove.

- Bounded 24-hour `gp_history` windows worked without the out-of-bounds
  Space-Track error that unbounded `<cutoff` queries produced.
- The replay processed 8.35M history rows across 103 windows using 50k
  `NORAD_CAT_ID` chunks and a 15 second inter-request delay.
- Starting from an empty Jan 1 state reconstructed 31,412 objects. Current `gp`
  contained 67,052 objects at the comparison observation time, so 35,640 current
  objects had no post-Jan-1 history rows and cannot be recovered by a
  delta-only replay.
- Of the 31,412 shared objects, 31,310 byte-matched current `gp`; 102 differed.
  Those differences confirm that current `gp` is a useful audit observation but
  not a perfect deterministic reconstruction target.

Operational conclusion: use `genesis` once to create the first agreed full
catalog from current `gp`, then use bounded `gp_history` deltas for every
subsequent daily consensus snapshot.
