// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2022 Dhevan Gangadharan

#include "DD4hep/DetFactoryHelper.h"
#include "DD4hep/Printout.h"
#include "DD4hep/Shapes.h"
#include "DDRec/DetectorData.h"
#include "DDRec/Surface.h"
#include "XML/Layering.h"
#include <XML/Helper.h>
#include "DD4hepDetectorHelper.h"

using namespace std;
using namespace dd4hep;

static Ref_t create_detector(Detector& description, xml_h e, SensitiveDetector /*sens*/) {

  xml_det_t x_det  = e;
  xml_comp_t x_dim = x_det.dimensions();
  xml_comp_t x_pos = x_det.position();
  xml_comp_t x_rot = x_det.rotation();
  //
  string det_name = x_det.nameStr();
  string mat_name = dd4hep::getAttrOrDefault<string>(x_det, _U(material), "StainlessSteelP506");
  //
  double sizeR     = x_dim.r();
  double sizeZ     = x_dim.z();
  double sizeR_el  = x_dim.x();
  double sizeR_had = x_dim.y();
  double posX      = x_pos.x();
  double posY      = x_pos.y();
  double posZ      = x_pos.z();
  double rotX      = x_rot.x();
  double rotY      = x_rot.y();
  double rotZ      = x_rot.z();

  Tube tube(0.0, sizeR, sizeZ);
  Tube tube_el(0.0, sizeR_el, sizeZ);
  Tube tube_had(0.0, sizeR_had, sizeZ);
  SubtractionSolid exit_window(tube, tube_had, Position(0.0, 0.0, 0.0));
  exit_window = SubtractionSolid(exit_window, tube_el, Position(-posX, 0.0, 0.0));

  Volume vol(det_name + "_vol_ExitWindow", exit_window, description.material(mat_name));
  vol.setVisAttributes(description.visAttributes(x_det.visStr()));

  Transform3D pos(RotationZYX(rotX, rotY, rotZ), Position(posX, posY, posZ));

  DetElement det(det_name, x_det.id());
  Volume motherVol = description.pickMotherVolume(det);
  PlacedVolume phv = motherVol.placeVolume(vol, pos);

  det.setPlacement(phv);

  // Reconstruction-only description of the existing Boolean steel window.
  // The physical solid, material and placement above remain unchanged.
  if (x_det.hasChild(_Unicode(acts_passive_disc))) {
    auto& params = DD4hepDetectorHelper::ensureExtension<dd4hep::rec::VariantParameters>(det);
    params.set<bool>("passive_disc", true);
    params.set<double>("passive_disc_r_min", sizeR_had / dd4hep::mm);
    params.set<double>("passive_disc_r_max", sizeR / dd4hep::mm);
    params.set<double>("passive_disc_half_length_z", sizeZ / dd4hep::mm);
    for (xml_coll_t material(x_det, _Unicode(layer_material)); material; ++material) {
      DD4hepDetectorHelper::xmlToProtoSurfaceMaterial(xml_comp_t(material), params,
                                                      "layer_material");
    }
  }

  return det;
}

DECLARE_DETELEMENT(B0Window, create_detector)
