# Physical-pitch B0 readout profiles

Companion to EICrecon's opt-in `b0_aclgad` response and the surface/material audit
in epic #2. The default `compact/far_forward/B0_tracker.xml` is **unchanged**.

`make_aclgad_profile.py create` copies a generated/installed detector tree into
a fresh directory, verifies that the selected compact reaches the B0 readout,
and changes only that copy's B0 segmentation to physical channels. The default
500-um pitch tiles the current 16-mm sensors with 32 cells along each axis; a
250-um offset puts whole cells inside the sensor edges. The existing layer,
module, sensor and signed channel-ID field layout is preserved. A profile marker
requires the companion response to verify its interpretation. Pitch describes
channels, **not** reconstructed position precision.

```sh
python3 scripts/b0_readout/make_aclgad_profile.py create \
  --source /path/to/generated-detector \
  --output /path/to/new-physical-profile \
  --compact epic_ip6_extended.xml \
  --material-map /path/to/material-map.cbor
python3 scripts/b0_readout/make_aclgad_profile.py verify /path/to/new-physical-profile
```

The output directory must be new and outside the source tree. All copied files,
local XML include closure and an optional material-map hash are recorded.
External/directory symlinks, unresolved variables, nonlocal includes, include
cycles and a compact that never includes B0 are rejected. Use a resolved,
generated detector tree, not arbitrary unresolved compact templates. The profile
is immutable for verification: later changes, missing files and extra assets
are errors. Keep build outputs, bytecode and run outputs outside it.

## Regenerate simulation, do not reinterpret old IDs

```sh
python3 scripts/b0_readout/make_aclgad_profile.py simulate \
  --profile /path/to/new-physical-profile \
  --out /path/to/new-simulation-run \
  --executable npsim \
  --argument=--enableGun \
  --argument=--gun.particle --argument=proton \
  --argument=--numberOfEvents --argument=10
```

These are syntax examples, not a specified physics acceptance sample. Set the
beam direction, kinematics, vertex distribution and event generator appropriate
to the study. Each `--argument` is one literal argument; no shell interpolation
occurs. The compact/output arguments are controlled by the wrapper. `--asset`
records explicit generator, field, calibration or library inputs. The wrapper
requires a successful simulator exit, an actual output file, and unchanged
profile/binary/explicit inputs before marking `simulation.json` completed.
Failed invocations retain a failed receipt. The EICrecon response benchmark
requires this receipt and verifies its profile and output hashes.

Execution provenance does not prove that a custom executable or steering script
obeyed its inputs. Do not override geometry/output inside custom steering code.
Source hashes are not proof of dynamic-library identity; pass externally loaded
assets explicitly. The wrapper uses the normal stock simulator, not an injected
GitHub workflow or hidden mutation of the user's environment.

## Material and validation

This change does not alter sensor/support placement and does not generate a new
ACTS material map. A matching map may be reusable only after verifying sensitive
volume IDs, ACTS surfaces and navigation with the audit tools in
`scripts/b0_validation`. Recording a map hash is **not** material validation.
The script currently targets rectangular 16-mm B0 sensors; the response adapter
also checks actual sensor dimensions and complete channel tiling at runtime.

The current full detector, DD4hep profile load, channel-ID round trips and
simulation output have **not** been exercised by the Python tests. Those tests
cover file/profile integrity, XML includes, preservation of the original readout,
pitch/offset/ID rules, and simulated success/failure bookkeeping using mocked
subprocess calls, not physics simulation.

```sh
python3 -m unittest discover -s scripts/b0_readout -p test_profile.py -v
```
