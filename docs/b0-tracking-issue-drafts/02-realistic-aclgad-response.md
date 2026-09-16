# B0: replace the effective 70 um readout proxy with a realistic AC-LGAD response/digitization model

## Motivation

The realistic B0 geometry describes 50 um silicon AC-LGAD sensors, but the compact readout currently uses a `CartesianGridXY` segmentation with `grid_size_x = grid_size_y = 0.070 mm` and explicitly notes that this is an **effective** resolution proxy intended to mimic roughly 20 um position resolution from real ~500 um AC-LGAD pixels with charge sharing.

That approximation is useful for geometry/tracking development, but it conflates three physically different things:

1. physical electrode/pixel pitch and channel topology;
2. charge sharing / pulse amplitudes across neighboring electrodes;
3. reconstructed hit position resolution.

It also makes it difficult to validate realistic front/back hit multiplicity, cluster behavior, timing use, thresholds, inefficiencies, and occupancy for B0 pattern recognition.

The current comment in `B0_tracker.xml` also refers to a 75 um effective pixel while the actual configured grid is 70 um; the production geometry should describe the approximation unambiguously.

Relevant geometry:

- `compact/far_forward/B0_tracker.xml`
- `src/B0Tracker_geo.cpp`

Relevant reconstruction/digitization lives in `tom-bleher/EICrecon`, especially `src/detectors/B0TRK/B0TRK.cc` and the silicon tracker digitization/hit-reconstruction chain.

## Confirmed shared-digitizer findings

A source audit found two generic `SiliconTrackerDigi` behaviors that are important for B0 response studies:

1. **Channel thresholding / truth associations:** the previous implementation applied the threshold to each simulated contribution before summing a cell, then linked the emitted raw hit to every simulated hit in the cell. This could drop two individually sub-threshold deposits whose sum is above threshold and could produce truth links inconsistent with emitted charge. EICrecon PR #8 implements channel-level accumulation/thresholding and energy-fraction truth weights.
2. **Channel timing:** the current generic implementation smears each contribution independently and takes the minimum smeared time. For simultaneous Gaussian-smeared contributions this creates an occupancy-dependent early bias. For two contributions, `E[min(epsilon1, epsilon2)] = -sigma/sqrt(pi)`; with a nominal 30 ps B0 resolution that illustrative bias is about -16.9 ps. A separate EICrecon issue draft now tracks channel-level timing semantics.

These are shared infrastructure concerns; the final AC-LGAD response should build on well-defined channel semantics rather than compensate for them downstream.

## Goal

Represent the B0 AC-LGAD sensor/readout in a way that preserves the physical ~500 um channel pitch while deriving spatial/timing measurements from an explicit detector-response model rather than from a fictitious 70 um segmentation.

This is a geometry + digitization task and will likely require coordinated changes in both `epic` and `EICrecon`.

## Proposed staged approach

### Stage 1: define the detector-response contract

Document the quantities that should be simulated/reconstructed for one particle crossing an AC-LGAD sensor:

- physical channel/electrode pitch and indexing;
- deposited charge / LGAD gain model;
- induced charge-sharing weights on neighboring electrodes;
- per-channel gain variation;
- electronic/noise contribution;
- channel-level threshold semantics;
- timing measurement and resolution;
- reconstructed local position and covariance;
- cluster/channel relations retained for diagnostics if possible.

The model does not need to reproduce a full transient electronics simulation initially, but its approximations should be explicit.

### Stage 2: restore physical segmentation

Represent the actual B0 AC-LGAD pitch/channel topology in the readout instead of encoding final resolution as segmentation pitch.

The geometry/readout should not claim 70 um physical cells if the detector design uses ~500 um electrodes. If an effective-resolution mode is retained for fast tracking studies, name it explicitly and keep its configured effective pitch separate from the physical-response mode.

### Stage 3: charge-sharing digitization

Implement/configure an AC-LGAD-specific digitization/reconstruction path in EICrecon. A practical first model can use a parameterized spatial response measured/tuned from dedicated AC-LGAD studies:

```text
energy deposit
  -> gain
  -> charge shared among neighboring electrodes
  -> gain/noise/threshold per channel
  -> cluster
  -> position estimator + covariance
  -> time estimator + covariance
```

The existing B0 charge-sharing studies can provide initial parameters, but the production model should keep them configurable rather than hard-coded to one idealized result.

### Stage 4: validate tracking-level consequences

Compare the current 70 um proxy against the realistic response for:

- single-hit spatial residuals;
- local covariance calibration/pulls;
- cluster size and channel multiplicity;
- hit efficiency near pixel/module boundaries;
- front/back station hit multiplicity;
- B0 seed efficiency;
- track efficiency/fakes/duplicates;
- timing-based candidate rejection.

## Timing

EICrecon currently configures a 30 ps B0 time resolution. The realistic response should clarify what this number represents and whether additional contributions are needed from:

- sensor/electronics jitter;
- time walk / amplitude dependence;
- clock/event-time uncertainty;
- reconstruction/cluster combination.

Preserve enough timing information that B0 candidate building can test time-of-flight compatibility without assuming an unrealistically perfect common time reference. Timing-based candidate rejection should remain default-off until the channel response is validated.

## Acceptance criteria

- [ ] `epic` readout uses/documentably represents the physical B0 AC-LGAD channel pitch/topology rather than an effective 70 um pseudo-pixel.
- [ ] If an idealized effective-resolution mode is retained, its 70 um proxy and ~20.2 um digital RMS are explicitly labeled and internally consistent.
- [ ] A configurable AC-LGAD charge-sharing digitization/reconstruction model exists in EICrecon.
- [ ] Thresholding is defined at the channel-response level and truth relations describe the represented signal.
- [ ] Spatial hit covariance comes from the response/reconstruction model, not directly from the physical pitch alone.
- [ ] Timing resolution/uncertainty is modeled and documented consistently at channel/cluster level.
- [ ] Unit/integration tests cover central crossings, channel boundaries, module edges, noise/threshold behavior and multi-channel clusters.
- [ ] Tracking benchmark compares the old effective-resolution proxy with the realistic response.
- [ ] Geometry and digitization changes preserve cellID uniqueness across layer/module/sensor/channel.

## Non-goals

- Requiring a full waveform/electronics simulation in the first implementation.
- Hard-coding one charge-sharing function as universally valid for all final AC-LGAD sensor designs.
- Tuning tracking cuts to hide a poorly calibrated hit covariance.
