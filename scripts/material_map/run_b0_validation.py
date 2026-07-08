#!/usr/bin/env python3
# B0-targeted material validation: eta 4-6 (B0 acceptance), regenerated map
import os
import acts
import acts.examples.dd4hep
from acts.examples import Sequencer

import epic
from material_validation_b0eta import runMaterialValidation

detector = epic.getDetector(
    os.environ["DETECTOR_PATH"] + "/epic_craterlake_material_map.xml", "material-map.cbor"
)
trackingGeometry = detector.trackingGeometry()
decorators = detector.contextDecorators()
field = acts.ConstantBField(acts.Vector3(0, 0, 0))

runMaterialValidation(
    200, 5000, trackingGeometry, decorators, field,
    outputDir=os.getcwd(), outputName="propagation_material_b0eta",
    s=Sequencer(events=200, numThreads=-1),
).run()
