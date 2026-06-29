// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Whitney Armstrong, Igor Korover, Tom Bleher
//
// B0 Tracker - ACTS/DDRec-friendly builder
//
// Canonical layer -> side -> module -> sensor hierarchy:
//   - TrackingUnit module Assembly is built ONCE and reused via
//     placeVolume for every (layer, module-position) entry
//   - Front/back module stacks are exposed as separate ACTS layers so
//     one-sided geometries still have reachable binned sensitive surfaces
//   - Module DetElement is anchored to the module Assembly's placement
//     into the side layer (not to a child component)
//   - Sensor DetElements sit under the module DetElement
//   - Layer envelope tolerances read from <envelope> and propagated

#include "DD4hep/DetFactoryHelper.h"
#include "DD4hep/Printout.h"
#include "DD4hep/Shapes.h"
#include "DD4hepDetectorHelper.h"
#include "DDRec/DetectorData.h"
#include "DDRec/Surface.h"
#include "XML/Utilities.h"

#include <cmath>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

using namespace std;
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

struct SideLayer {
  Assembly volume;
  DetElement detElement;
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
  // Read TrackingUnit component definitions from the compact file.
  // TrackingUnit is declared at file scope (shared across all 4 layers),
  // not as a child of <detector>, so we walk the document root.
  // ------------------------------------------------------------------
  xml_h root          = x_det.document().root();
  auto findRootModule = [&](const std::string& name) {
    xml_h found;
    for (xml_coll_t it(root, _U(module)); it; ++it) {
      xml_comp_t xm = it;
      if (xm.nameStr() == name) {
        found = xm;
        break;
      }
    }
    return found;
  };

  xml_h trackingUnit = findRootModule("TrackingUnit");
  if (!trackingUnit.ptr()) {
    throw std::runtime_error("FATAL: <module name=\"TrackingUnit\"> not found in compact file");
  }

  std::vector<ModuleComponentDef> moduleComponents;

  double zMin          = +std::numeric_limits<double>::infinity();
  double zMax          = -std::numeric_limits<double>::infinity();
  double sensitiveZMin = +std::numeric_limits<double>::infinity();
  double sensitiveZMax = -std::numeric_limits<double>::infinity();

  for (xml_coll_t comp(trackingUnit, _U(module_component)); comp; ++comp) {
    xml_comp_t xc = comp;
    xml_h x_box   = xc.child(_U(box));

    const double dx = x_box.attr<double>(_Unicode(x));
    const double dy = x_box.attr<double>(_Unicode(y));
    const double dz = x_box.attr<double>(_Unicode(z));

    xml::Component cpos = xc.position();
    const double px     = cpos.x();
    const double py     = cpos.y();
    const double pz     = cpos.z();

    zMin = std::min(zMin, pz - dz / 2.0);
    zMax = std::max(zMax, pz + dz / 2.0);

    ModuleComponentDef cdef;
    cdef.name      = xc.nameStr();
    cdef.material  = xc.attr<std::string>(_Unicode(material));
    cdef.vis       = xc.hasAttr(_Unicode(vis)) ? xc.attr<std::string>(_Unicode(vis)) : "";
    cdef.dx        = dx;
    cdef.dy        = dy;
    cdef.dz        = dz;
    cdef.px        = px;
    cdef.py        = py;
    cdef.pz        = pz;
    cdef.sensitive = xc.hasAttr(_Unicode(sensitive)) && xc.attr<bool>(_Unicode(sensitive));

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
  std::map<std::string, Assembly> modules;
  std::map<std::string, Placements> sensitives;
  std::map<std::string, std::vector<VolPlane>> volplane_surfaces;

  {
    const std::string m_nam = "TrackingUnit";
    Assembly moduleAsm(m_nam);
    if (xml_comp_t(trackingUnit).hasAttr(_Unicode(vis))) {
      moduleAsm.setVisAttributes(description.visAttributes(xml_comp_t(trackingUnit).visStr()));
    }

    int sensorIndex = 1;
    for (const auto& cdef : moduleComponents) {
      Material mat = description.material(cdef.material);
      Box shape(cdef.dx / 2.0, cdef.dy / 2.0, cdef.dz / 2.0);
      Volume c_vol(cdef.name, shape, mat);

      if (!cdef.vis.empty()) {
        c_vol.setVisAttributes(description.visAttributes(cdef.vis));
      }
      if (cdef.sensitive) {
        c_vol.setSensitiveDetector(sens);
      }

      PlacedVolume comp_pv = moduleAsm.placeVolume(c_vol, Position(cdef.px, cdef.py, cdef.pz));

      if (cdef.sensitive) {
        comp_pv.addPhysVolID("sensor", sensorIndex);
        sensitives[m_nam].push_back(comp_pv);

        // Sensor lies flat in the xy plane → surface normal along +z.
        Vector3D u(-1.0, 0.0, 0.0);
        Vector3D v(0.0, -1.0, 0.0);
        Vector3D n(0.0, 0.0, 1.0);
        SurfaceType type(SurfaceType::Sensitive);
        VolPlane surf(c_vol, type, cdef.inner, cdef.outer, u, v, n);
        volplane_surfaces[m_nam].push_back(surf);

        ++sensorIndex;
      }
    }

    modules[m_nam] = moduleAsm;
  }

  // ------------------------------------------------------------------
  // Support disk volume (B0SupportDisk) — built once, placed per layer.
  // ------------------------------------------------------------------
  double rmin              = description.constant<double>("B0TrackerSupportRMin");
  double rmax              = description.constant<double>("B0TrackerSupportRMax");
  const double thick       = description.constant<double>("B0TrackerSupportThickness");
  double supportHalfLength = thick / 2.0;
  double phi0              = description.constant<double>("B0TrackerSupportPhiStart");
  double dphi              = description.constant<double>("B0TrackerSupportPhiDelta");
  std::string supportMaterial{"Copper"};
  std::string supportVis;

  xml_h supportDisk = findRootModule("B0SupportDisk");
  if (supportDisk.ptr()) {
    xml_comp_t x_support_module = supportDisk;
    supportVis = getAttrOrDefault<std::string>(x_support_module, _Unicode(vis), "");

    xml_comp_t x_support_component = x_support_module.child(_U(module_component), false);
    if (x_support_component.ptr()) {
      supportMaterial =
          getAttrOrDefault<std::string>(x_support_component, _Unicode(material), supportMaterial);
      supportVis = getAttrOrDefault<std::string>(x_support_component, _Unicode(vis), supportVis);

      xml_comp_t x_tube = x_support_component.child(_U(tube), false);
      if (x_tube.ptr()) {
        rmin              = getAttrOrDefault<double>(x_tube, _Unicode(rmin), rmin);
        rmax              = getAttrOrDefault<double>(x_tube, _Unicode(rmax), rmax);
        supportHalfLength = getAttrOrDefault<double>(x_tube, _Unicode(dz), supportHalfLength);
        phi0              = getAttrOrDefault<double>(x_tube, _Unicode(startphi), phi0);
        dphi              = getAttrOrDefault<double>(x_tube, _Unicode(deltaphi), dphi);
      }
    }
  }

  Tube supportSolid(rmin, rmax, supportHalfLength, phi0, phi0 + dphi);
  Material supportMat = description.material(supportMaterial);
  Volume supportVol("B0SupportDiskVol", supportSolid, supportMat);
  if (!supportVis.empty()) {
    supportVol.setVisAttributes(description.visAttributes(supportVis));
  }

  const double moduleOffset = description.constant<double>("B0TrackerModuleOffsetFromSupport");
  const double frontZ       = +moduleOffset;
  const double backZ        = -moduleOffset;

  // ------------------------------------------------------------------
  // Layers
  // ------------------------------------------------------------------
  int globalModuleID = 1;

  for (xml_coll_t layer(x_det, _U(layer)); layer; ++layer) {
    xml_comp_t x_layer = layer;
    const int layerID  = x_layer.id();

    // --------------------------------------------------------------
    // Read layer envelope like in the working original
    // --------------------------------------------------------------
    xml_comp_t x_env = x_layer.child(_U(envelope), false);

    double env_rmin_tol = 0.0;
    double env_rmax_tol = 0.0;
    double env_zmin_tol = 0.0;
    double env_zmax_tol = 0.0;
    double env_length   = 0.0;
    double env_zstart   = 0.0;
    std::string env_vis;

    if (x_env.ptr()) {
      env_rmin_tol = getAttrOrDefault<double>(x_env, _Unicode(rmin_tolerance), 0.0);
      env_rmax_tol = getAttrOrDefault<double>(x_env, _Unicode(rmax_tolerance), 0.0);
      env_zmin_tol = getAttrOrDefault<double>(x_env, _Unicode(zmin_tolerance), 0.0);
      env_zmax_tol = getAttrOrDefault<double>(x_env, _Unicode(zmax_tolerance), 0.0);
      env_length   = getAttrOrDefault<double>(x_env, _Unicode(length), 0.0);
      env_zstart   = getAttrOrDefault<double>(x_env, _Unicode(zstart), 0.0);

      if (x_env.hasAttr(_Unicode(vis))) {
        env_vis = x_env.attr<std::string>(_Unicode(vis));
      }
    }

    std::string layer_name = det_name + std::string("_layer") + std::to_string(layerID);

    // Layer origin from XML. Mechanical support stays at this origin; ACTS tracking
    // layers below are split by side and centered on their sensor stacks.
    xml_comp_t lp       = x_layer.child(_U(position));
    const double layerX = lp.attr<double>(_Unicode(x));
    const double layerY = lp.attr<double>(_Unicode(y));
    const double layerZ = lp.attr<double>(_Unicode(z));

    std::map<std::string, SideLayer> sideLayers;

    auto ensureSideLayer = [&](const std::string& side) -> SideLayer& {
      auto existing = sideLayers.find(side);
      if (existing != sideLayers.end()) {
        return existing->second;
      }

      const bool isFront          = side == "front";
      const double sideSign       = isFront ? 1.0 : -1.0;
      const double nominalModuleZ = isFront ? frontZ : backZ;
      const double sideTagZ       = sideSign * 1.0e-6 * mm;
      const double sideLayerZ     = nominalModuleZ + sensitiveCenterZ - sideTagZ;

      const std::string sideLayerName = layer_name + "_" + side;
      Assembly sideVol(sideLayerName);
      if (!env_vis.empty()) {
        sideVol.setVisAttributes(description.visAttributes(env_vis));
      }

      PlacedVolume sidePV =
          assembly.placeVolume(sideVol, Position(layerX, layerY, layerZ + sideLayerZ));
      sidePV.addPhysVolID("layer", layerID);

      const int sideID = layerID * 10 + (isFront ? 1 : 2);
      DetElement sideDE(sdet, sideLayerName + "_P", sideID);
      sideDE.setPlacement(sidePV);

      auto inserted = sideLayers.emplace(side, SideLayer{sideVol, sideDE});
      return inserted.first->second;
    };

    // --------------------------------------------------------------
    // Support disks are detailed material at the XML layer origin. The ACTS
    // measurement layers are the side-specific sensor stacks, so material maps
    // project this support material onto those side-layer surfaces.
    // --------------------------------------------------------------
    for (xml_coll_t comp(x_layer, _U(component)); comp; ++comp) {
      xml_comp_t xc = comp;
      if (xc.hasAttr(_Unicode(ref)) && xc.attr<std::string>(_Unicode(ref)) == "B0SupportDisk") {
        xml_comp_t sp = xc.child(_U(position));
        assembly.placeVolume(supportVol, Position(layerX + sp.attr<double>(_Unicode(x)),
                                                  layerY + sp.attr<double>(_Unicode(y)),
                                                  layerZ + sp.attr<double>(_Unicode(z))));
      }
    }

    // --------------------------------------------------------------
    // Place the shared TrackingUnit Assembly at each <module_positions>
    // entry; wire up module and sensor DetElements.
    // --------------------------------------------------------------
    xml_comp_t mpos = x_layer.child("module_positions");
    if (!mpos.ptr()) {
      printout(WARNING, det_name, "Layer %d has no <module_positions> - skipping modules", layerID);
      continue;
    }

    const std::string m_nam = "TrackingUnit";
    Volume m_vol            = modules[m_nam];
    Placements& sensVols    = sensitives[m_nam];
    auto& sensSurfs         = volplane_surfaces[m_nam];

    for (xml_coll_t mp(mpos, _U(module)); mp; ++mp, ++globalModuleID) {
      xml_comp_t xm = mp;

      const double modX      = xm.attr<double>(_Unicode(posX));
      const double modY      = xm.attr<double>(_Unicode(posY));
      const double modRotZ   = xm.attr<double>(_Unicode(rotZ));
      const std::string side = xm.attr<std::string>(_Unicode(side));
      if (side != "front" && side != "back") {
        throw std::runtime_error("FATAL: B0Tracker layer " + std::to_string(layerID) +
                                 " has module with side=\"" + side +
                                 "\"; expected \"front\" or \"back\"");
      }
      const bool isFront = side == "front";
      const double modZ  = (isFront ? 1.0 : -1.0) * 1.0e-6 * mm - sensitiveCenterZ;

      SideLayer& sideLayer = ensureSideLayer(side);

      // Keep your required front/back rotation convention
      RotationZYX rotLocal(modRotZ, 0.0, (side == "back" ? M_PI : 0.0));
      Transform3D modTr(rotLocal, Position(modX, modY, modZ));

      // Place the single shared TrackingUnit Assembly into the side-specific ACTS layer.
      PlacedVolume mod_pv = sideLayer.volume.placeVolume(m_vol, modTr);
      mod_pv.addPhysVolID("module", globalModuleID);

      // Module DetElement, anchored to the module placement
      std::string m_base = _toString(layerID, "layer%d") + _toString(globalModuleID, "_module%d");
      DetElement modDE(sideLayer.detElement, m_base + "_pos", globalModuleID);
      modDE.setPlacement(mod_pv);

      // Sensor DetElements as children of modDE.
      for (size_t ic = 0; ic < sensVols.size(); ++ic) {
        PlacedVolume sens_pv = sensVols[ic];
        DetElement comp_de(modDE, std::string("de_") + sens_pv.volume().name(), globalModuleID);
        comp_de.setPlacement(sens_pv);

        auto& comp_de_params =
            DD4hepDetectorHelper::ensureExtension<dd4hep::rec::VariantParameters>(comp_de);
        comp_de_params.set<std::string>("axis_definitions", "XYZ");

        volSurfaceList(comp_de)->push_back(sensSurfs[ic]);
      }
    }

    // --------------------------------------------------------------
    // Envelope metadata, like the working original. Apply it to each
    // side-specific ACTS layer so one-sided layouts do not get merged into
    // an inaccessible inferred layer.
    // --------------------------------------------------------------
    for (auto& [side, sideLayer] : sideLayers) {
      sideLayer.volume->GetShape()->ComputeBBox();

      auto& sideParams = DD4hepDetectorHelper::ensureExtension<dd4hep::rec::VariantParameters>(
          sideLayer.detElement);

      sideParams.set<double>("envelope_r_min", env_rmin_tol / dd4hep::mm);
      sideParams.set<double>("envelope_r_max", env_rmax_tol / dd4hep::mm);
      sideParams.set<double>("envelope_z_min", env_zmin_tol / dd4hep::mm);
      sideParams.set<double>("envelope_z_max", env_zmax_tol / dd4hep::mm);

      for (xml_coll_t lmat(x_layer, _Unicode(layer_material)); lmat; ++lmat) {
        xml_comp_t x_layer_material = lmat;
        DD4hepDetectorHelper::xmlToProtoSurfaceMaterial(x_layer_material, sideParams,
                                                        "layer_material");
      }

      if (x_env.ptr()) {
        printout(INFO, det_name,
                 "Layer %d %s envelope: length=%8.3f mm zstart=%8.3f mm "
                 "tol(rmin,rmax,zmin,zmax)=(%6.3f,%6.3f,%6.3f,%6.3f) mm",
                 layerID, side.c_str(), env_length / mm, env_zstart / mm, env_rmin_tol / mm,
                 env_rmax_tol / mm, env_zmin_tol / mm, env_zmax_tol / mm);
      }
    }
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
