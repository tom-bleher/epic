# B0 geometry/digitization follow-up issue drafts

These drafts accompany the B0 tracking follow-up in `tom-bleher/EICrecon`.

1. **[Validate ACTS surfaces/material and material-map reproducibility](01-material-surface-map-validation.md)**
   - geometry/material correctness first;
   - ensure EICrecon tracks are evaluated with the material map matching this geometry.

2. **[Replace the effective 70 um readout proxy with realistic AC-LGAD response](02-realistic-aclgad-response.md)**
   - keep physical channel pitch distinct from reconstructed resolution;
   - coordinate charge-sharing/timing digitization with EICrecon.

These are intentionally separated from tracking-algorithm issues because geometry/material correctness and detector-response realism should be established independently of CKF/seeder tuning.
