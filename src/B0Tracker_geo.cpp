// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2022 - 2026 Whitney Armstrong, Igor Korover, Tom Bleher
//
// B0 Tracker - ACTS/DDRec-friendly builder
//
// Canonical layer -> module -> sensor hierarchy:
//   - Each compact <layer> is one ACTS disc layer: the front or back
//     sensor stack of a station, so one-sided geometries still have
//     reachable binned sensitive surfaces (FTOF idiom, cf. tof_endcap.xml)
//   - Layer id = 2*(station-1) + (1=back | 2=front): one cellID "layer"
//     value per detection plane, monotonic in z, so front and back planes
//     are distinguishable directly from the cellID
//   - Module ids restart at 1 within each layer, so cellIDs do not depend
//     on the order of <layer> blocks in the compact file
//   - TrackingUnit module Assembly is built ONCE and reused via
//     placeVolume for every (layer, module-position) entry
//   - Module DetElement is anchored to the module Assembly's placement
//     into the layer (not to a child component)
//   - Sensor DetElements sit under the module DetElement
//   - Layer <envelope> z tolerances are required non-zero (eic/epic#1009)

#include "DD4hep/DetFactoryHelper.h"
#include "DD4hep/IDDescriptor.h"
#include "DD4hep/Printout.h"
#include "DD4hep/Readout.h"
#include "DD4hep/Shapes.h"
#include "DD4hepDetectorHelper.h"
#include "DDRec/DetectorData.h"
#include "DDRec/Surface.h"
#include "XML/Utilities.h"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

using namespace dd4hep;
using namespace dd4hep::rec;

namespace {
struct ModuleComponentDef {
  std::string name;
  std::string material;
  std::string vis;
  double dx{0.0};
  double dy{0.0};
  double dz{0.0};
  double px{0.0};
  double py{0.0};
  double pz{0.0};
  bool sensitive{false};
  double inner{0.0};
  double outer{0.0};
};

} // namespace

static Ref_t create_B0Tracker(Detector& description, xml_h e, SensitiveDetector sens) {
  using Placements = std::vector<PlacedVolume>;

  xml_det_t x_det            = e;
  const int det_id           = x_det.id();
  const std::string det_name = x_det.nameStr();

  DetElement sdet(det_name, det_id);
  Assembly assembly(det_name);

  // Mother volume and top transform
  Volume motherVol   = description.pickMotherVolume(sdet);
  xml::Component pos = x_det.position();
  xml::Component rot = x_det.rotation();
  Transform3D posAndRot(RotationZYX(rot.z(), rot.y(), rot.x()),
                        Position(pos.x(), pos.y(), pos.z()));

  PlacedVolume pv;

  // Set detector type flag + VariantParameters extension
  dd4hep::xml::setDetectorTypeFlag(x_det, sdet);
  auto& detParams = DD4hepDetectorHelper::ensureExtension<dd4hep::rec::VariantParameters>(sdet);

  // Optional boundary material configuration
  for (xml_coll_t bmat(x_det, _Unicode(boundary_material)); bmat; ++bmat) {
    xml_comp_t x_boundary_material = bmat;
    DD4hepDetectorHelper::xmlToProtoSurfaceMaterial(x_boundary_material, detParams,
                                                    "boundary_material");
  }

  assembly.setVisAttributes("InvisibleWithDaughters");
  sens.setType("tracker");

  // ------------------------------------------------------------------
  // Read the shared <module> definitions declared under <detector>
  // (TrackingUnit sensor stack and B0SupportDisk), like sibling drivers.
  // ------------------------------------------------------------------
  auto findModule = [&x_det](const std::string& name) {
    xml_h found;
    for (xml_coll_t it(x_det, _U(module)); it; ++it) {
      xml_comp_t xm = it;
      if (xm.nameStr() == name) {
        found = xm;
        break;
      }
    }
    return found;
  };

  xml_h trackingUnit = findModule("TrackingUnit");
  if (!trackingUnit.ptr()) {
    throw std::runtime_error("FATAL: <module name=\"TrackingUnit\"> not found under <detector>");
  }

  std::vector<ModuleComponentDef> moduleComponents;

  double zMin          = +std::numeric_limits<double>::infinity();
  double zMax          = -std::numeric_limits<double>::infinity();
  double sensitiveZMin = +std::numeric_limits<double>::infinity();
  double sensitiveZMax = -std::numeric_limits<double>::infinity();

  for (xml_coll_t comp(trackingUnit, _U(module_component)); comp; ++comp) {
    xml_comp_t xc   = comp;
    xml_dim_t x_box = xc.child(_U(box));

    const double dx = x_box.x();
    const double dy = x_box.y();
    const double dz = x_box.z();

    xml::Component cpos = xc.position();
    const double px     = cpos.x();
    const double py     = cpos.y();
    const double pz     = cpos.z();

    zMin = std::min(zMin, pz - dz / 2.0);
    zMax = std::max(zMax, pz + dz / 2.0);

    ModuleComponentDef cdef;
    cdef.name      = xc.nameStr();
    cdef.material  = xc.attr<std::string>(_Unicode(material));
    cdef.vis       = getAttrOrDefault<std::string>(xc, _Unicode(vis), "");
    cdef.dx        = dx;
    cdef.dy        = dy;
    cdef.dz        = dz;
    cdef.px        = px;
    cdef.py        = py;
    cdef.pz        = pz;
    cdef.sensitive = xc.isSensitive();

    if (cdef.sensitive) {
      sensitiveZMin = std::min(sensitiveZMin, pz - dz / 2.0);
      sensitiveZMax = std::max(sensitiveZMax, pz + dz / 2.0);
    }

    moduleComponents.push_back(cdef);
  }

  if (!std::isfinite(zMin) || !std::isfinite(zMax) || zMax <= zMin) {
    throw std::runtime_error("FATAL: failed to compute TrackingUnit z-extent (zMin/zMax invalid)");
  }
  if (!std::isfinite(sensitiveZMin) || !std::isfinite(sensitiveZMax) ||
      sensitiveZMax <= sensitiveZMin) {
    throw std::runtime_error("FATAL: failed to compute TrackingUnit sensitive z-extent");
  }

  const double sensitiveCenterZ = 0.5 * (sensitiveZMin + sensitiveZMax);

  // Per-sensor ACTS material thicknesses follow the full TrackingUnit stack,
  // matching the convention used by the central tracker builders.
  for (auto& cdef : moduleComponents) {
    if (!cdef.sensitive) {
      continue;
    }
    cdef.inner = cdef.pz - zMin;
    cdef.outer = zMax - cdef.pz;
  }

  // ------------------------------------------------------------------
  // Build the TrackingUnit module Assembly ONCE; collect its sensitive
  // PlacedVolumes and ACTS VolPlane measurement surfaces for reuse on
  // every per-(layer, module-position) placement
  // ------------------------------------------------------------------
  Assembly moduleAsm("TrackingUnit");
  Placements moduleSensVols;
  std::vector<VolPlane> moduleSensSurfs;

  moduleAsm.setVisAttributes(
      description, getAttrOrDefault<std::string>(xml_comp_t(trackingUnit), _Unicode(vis), ""));

  {
    int sensorIndex = 1;
    for (const auto& cdef : moduleComponents) {
      Material mat = description.material(cdef.material);
      Box shape(cdef.dx / 2.0, cdef.dy / 2.0, cdef.dz / 2.0);
      Volume c_vol(cdef.name, shape, mat);

      c_vol.setVisAttributes(description, cdef.vis);
      if (cdef.sensitive) {
        c_vol.setSensitiveDetector(sens);
      }

      PlacedVolume comp_pv = moduleAsm.placeVolume(c_vol, Position(cdef.px, cdef.py, cdef.pz));

      if (cdef.sensitive) {
        comp_pv.addPhysVolID("sensor", sensorIndex);
        moduleSensVols.push_back(comp_pv);

        // Sensor lies flat in the xy plane -> surface normal along +z.
        Vector3D u(-1.0, 0.0, 0.0);
        Vector3D v(0.0, -1.0, 0.0);
        Vector3D n(0.0, 0.0, 1.0);
        SurfaceType type(SurfaceType::Sensitive);
        VolPlane surf(c_vol, type, cdef.inner, cdef.outer, u, v, n);
        moduleSensSurfs.push_back(surf);

        ++sensorIndex;
      }
    }
  }

  // Guard cellID field capacities against the readout definition itself,
  // so a readout or module change cannot silently overflow a bit field.
  const dd4hep::IDDescriptor idSpec = sens.readout().idSpec();
  const auto maxSensorID            = idSpec.field("sensor")->maxValue();
  const auto maxModuleID            = idSpec.field("module")->maxValue();
  if (static_cast<long long>(moduleSensVols.size()) > static_cast<long long>(maxSensorID)) {
    throw std::runtime_error("FATAL: TrackingUnit has " + std::to_string(moduleSensVols.size()) +
                             " sensitive components; the 'sensor' readout field holds at most " +
                             std::to_string(maxSensorID));
  }

  // ------------------------------------------------------------------
  // Support disk volume (B0SupportDisk) -- built once, placed per station.
  // The compact <module> tube is the single source of truth; its
  // attributes reference the B0TrackerSupport* constants.
  // ------------------------------------------------------------------
  xml_h supportDisk = findModule("B0SupportDisk");
  if (!supportDisk.ptr()) {
    throw std::runtime_error("FATAL: <module name=\"B0SupportDisk\"> not found under <detector>");
  }
  xml_comp_t x_support_module    = supportDisk;
  xml_comp_t x_support_component = x_support_module.child(_U(module_component));
  xml_comp_t x_support_tube      = x_support_component.child(_U(tube));

  const std::string supportVis = getAttrOrDefault<std::string>(
      x_support_component, _Unicode(vis),
      getAttrOrDefault<std::string>(x_support_module, _Unicode(vis), ""));

  Tube supportSolid(x_support_tube.rmin(), x_support_tube.rmax(), x_support_tube.dz(),
                    x_support_tube.attr<double>(_Unicode(startphi)),
                    x_support_tube.attr<double>(_Unicode(startphi)) +
                        x_support_tube.attr<double>(_Unicode(deltaphi)));
  Material supportMat = description.material(x_support_component.materialStr());
  Volume supportVol("B0SupportDiskVol", supportSolid, supportMat);
  if (!supportVis.empty()) {
    supportVol.setVisAttributes(description.visAttributes(supportVis));
  }

  const double moduleOffset = description.constant<double>("B0TrackerModuleOffsetFromSupport");

  // ------------------------------------------------------------------
  // Layers: each compact <layer> is one ACTS disc layer -- the front or
  // back sensor stack of a station. One cellID "layer" value per plane;
  // module ids restart per layer, so nothing depends on block order.
  // ------------------------------------------------------------------
  for (xml_coll_t layer(x_det, _U(layer)); layer; ++layer) {
    xml_comp_t x_layer = layer;
    const int layerID  = x_layer.id();
    const int station  = x_layer.attr<int>(_Unicode(station));

    const std::string side = x_layer.attr<std::string>(_Unicode(side));
    if (side != "front" && side != "back") {
      throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                               " has side=\"" + side + "\"; expected \"front\" or \"back\"");
    }
    const bool isFront = side == "front";

    const int expectedID = 2 * (station - 1) + (isFront ? 2 : 1);
    if (layerID != expectedID) {
      throw std::runtime_error(
          "FATAL: B0Tracker layer id " + std::to_string(layerID) +
          " must equal 2*(station-1) + (1=back|2=front) = " + std::to_string(expectedID));
    }

    // --------------------------------------------------------------
    // Envelope: required, and z tolerances must be strictly positive.
    // Zero-valued envelopes make the ACTS approach surfaces of adjacent
    // layers touch and break navigation (see eic/epic#1009).
    // --------------------------------------------------------------
    xml_comp_t x_env = x_layer.child(_U(envelope), false);
    if (!x_env.ptr()) {
      throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                               " is missing its <envelope> element");
    }

    const double env_rmin_tol = getAttrOrDefault<double>(x_env, _Unicode(rmin_tolerance), 0.0);
    const double env_rmax_tol = getAttrOrDefault<double>(x_env, _Unicode(rmax_tolerance), 0.0);
    const double env_zmin_tol = getAttrOrDefault<double>(x_env, _Unicode(zmin_tolerance), 0.0);
    const double env_zmax_tol = getAttrOrDefault<double>(x_env, _Unicode(zmax_tolerance), 0.0);
    if (env_zmin_tol <= 0.0 || env_zmax_tol <= 0.0) {
      throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                               " has non-positive envelope z tolerance; this collapses the ACTS "
                               "approach surfaces (see eic/epic#1009)");
    }

    std::string env_vis;
    if (x_env.hasAttr(_Unicode(vis))) {
      env_vis = x_env.attr<std::string>(_Unicode(vis));
    }

    // Layer origin from XML: the station origin, shared by the front and
    // back layers of a station. Mechanical support stays at this origin.
    xml_dim_t lp        = x_layer.child(_U(position));
    const double layerX = lp.x();
    const double layerY = lp.y();
    const double layerZ = lp.z();

    // --------------------------------------------------------------
    // Support disks are detailed material at the station origin. The ACTS
    // measurement layers are the sensor stacks, so material maps project
    // this support material onto the adjacent layer surfaces.
    // --------------------------------------------------------------
    for (xml_coll_t comp(x_layer, _U(component)); comp; ++comp) {
      xml_comp_t xc         = comp;
      const std::string ref = xc.attr<std::string>(_Unicode(ref));
      if (ref != "B0SupportDisk") {
        throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                                 " has unsupported <component ref=\"" + ref + "\">");
      }
      xml_dim_t sp = xc.child(_U(position));
      assembly.placeVolume(supportVol, Position(layerX + sp.x(), layerY + sp.y(), layerZ + sp.z()));
    }

    xml_comp_t mpos = x_layer.child(_Unicode(module_positions), false);
    if (!mpos.ptr()) {
      throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                               " is missing its <module_positions> element");
    }

    // --------------------------------------------------------------
    // Layer assembly, centered on the sensor stack so the binned ACTS
    // measurement surfaces sit at the physical sensor planes.
    // --------------------------------------------------------------
    // Mirror-symmetric about the station origin: the sensor plane of each
    // side sits at +/-(moduleOffset + sensitiveCenterZ).
    const double sideSign   = isFront ? 1.0 : -1.0;
    const double sideLayerZ = sideSign * (moduleOffset + sensitiveCenterZ);

    const std::string sideLayerName = det_name + "_layer" + std::to_string(station) + "_" + side;
    Assembly sideVol(sideLayerName);
    if (!env_vis.empty()) {
      sideVol.setVisAttributes(description.visAttributes(env_vis));
    }

    PlacedVolume sidePV =
        assembly.placeVolume(sideVol, Position(layerX, layerY, layerZ + sideLayerZ));
    sidePV.addPhysVolID("layer", layerID);

    DetElement sideDE(sdet, sideLayerName + "_P", layerID);
    sideDE.setPlacement(sidePV);

    // --------------------------------------------------------------
    // Place the shared TrackingUnit Assembly at each <module_positions>
    // entry; wire up module and sensor DetElements. Module ids restart
    // at 1 within each layer.
    // --------------------------------------------------------------
    int moduleID = 1;

    for (xml_coll_t mp(mpos, _U(module)); mp; ++mp, ++moduleID) {
      xml_comp_t xm = mp;

      if (moduleID > maxModuleID) {
        throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                                 " has more modules than the 'module' readout field holds (" +
                                 std::to_string(maxModuleID) + ")");
      }

      const double modX    = xm.attr<double>(_Unicode(posX));
      const double modY    = xm.attr<double>(_Unicode(posY));
      const double modRotZ = xm.attr<double>(_Unicode(rotZ));
      // Recenter the sensitive stack on the layer plane; the Rx(pi) flip of
      // back modules maps the module-frame sensitive center +z -> -z, hence
      // the side-dependent sign.
      const double modZ = -sideSign * sensitiveCenterZ;

      // Back modules are flipped about x so both sides face the support.
      RotationZYX rotLocal(modRotZ, 0.0, isFront ? 0.0 : M_PI);
      Transform3D modTr(rotLocal, Position(modX, modY, modZ));

      PlacedVolume mod_pv = sideVol.placeVolume(moduleAsm, modTr);
      mod_pv.addPhysVolID("module", moduleID);

      // Module DetElement, anchored to the module placement
      std::string m_base = _toString(layerID, "layer%d") + _toString(moduleID, "_module%d");
      DetElement modDE(sideDE, m_base, moduleID);
      modDE.setPlacement(mod_pv);

      // Sensor DetElements as children of modDE.
      for (size_t ic = 0; ic < moduleSensVols.size(); ++ic) {
        PlacedVolume sens_pv = moduleSensVols[ic];
        DetElement comp_de(modDE, std::string("de_") + sens_pv.volume().name(), moduleID);
        comp_de.setPlacement(sens_pv);

        auto& comp_de_params =
            DD4hepDetectorHelper::ensureExtension<dd4hep::rec::VariantParameters>(comp_de);
        comp_de_params.set<std::string>("axis_definitions", "XYZ");

        volSurfaceList(comp_de)->push_back(moduleSensSurfs[ic]);
      }
    }

    // --------------------------------------------------------------
    // Envelope metadata and proto-material for this ACTS layer.
    // --------------------------------------------------------------
    sideVol->GetShape()->ComputeBBox();

    auto& sideParams =
        DD4hepDetectorHelper::ensureExtension<dd4hep::rec::VariantParameters>(sideDE);

    // ACTS DD4hepLayerBuilder reads these VariantParameters without unit
    // conversion, directly as ACTS-native mm, so store them in mm.
    sideParams.set<double>("envelope_r_min", env_rmin_tol / dd4hep::mm);
    sideParams.set<double>("envelope_r_max", env_rmax_tol / dd4hep::mm);
    sideParams.set<double>("envelope_z_min", env_zmin_tol / dd4hep::mm);
    sideParams.set<double>("envelope_z_max", env_zmax_tol / dd4hep::mm);

    for (xml_coll_t lmat(x_layer, _Unicode(layer_material)); lmat; ++lmat) {
      xml_comp_t x_layer_material = lmat;
      DD4hepDetectorHelper::xmlToProtoSurfaceMaterial(x_layer_material, sideParams,
                                                      "layer_material");
    }

    printout(INFO, det_name,
             "Layer %d (station %d %s) z=%8.3f mm "
             "tol(rmin,rmax,zmin,zmax)=(%6.3f,%6.3f,%6.3f,%6.3f) mm",
             layerID, station, side.c_str(), (layerZ + sideLayerZ) / mm, env_rmin_tol / mm,
             env_rmax_tol / mm, env_zmin_tol / mm, env_zmax_tol / mm);
  }

  // ------------------------------------------------------------------
  // Place the full detector assembly into the mother volume LAST
  // ------------------------------------------------------------------
  pv = motherVol.placeVolume(assembly, posAndRot);
  pv.addPhysVolID("system", det_id);
  sdet.setPlacement(pv);

  return sdet;
}

DECLARE_DETELEMENT(ip6_B0Tracker, create_B0Tracker)
