# SPDX-License-Identifier: LGPL-3.0-or-later
"""B0 uses the stock npsim tracker action.

Each TrackingUnit copy has its own volumes, so Geant4TrackerWeightedAction
does not merge deposits across modules. Pass geometry, input and output to
npsim explicitly.
"""

from DDSim.DD4hepSimulation import DD4hepSimulation

SIM = DD4hepSimulation()
