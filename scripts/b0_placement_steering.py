"""Placement-aware B0 hits; pass geometry, input and output to npsim explicitly."""

from DDSim.DD4hepSimulation import DD4hepSimulation

SIM = DD4hepSimulation()
SIM.action.mapActions["B0Tracker"] = (
    "Geant4TrackerWeightedPlacementAction",
    {"HitPositionCombination": 2, "CollectSingleDeposits": False},
)
