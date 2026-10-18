// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2022 - 2026 Whitney Armstrong, Tom Bleher, Igor Korover

/*! B0 Tracker: stations, each a support plate with sensor staves on both faces.
 *
 * @author Whitney Armstrong, Tom Bleher, Igor Korover
 *
 * Each <station> places a <support_plate> (the <support_stack> slices extruded
 * along its outline) and a <module_layout> of front and back <face>. Every face
 * is one ACTS disc layer holding the shared TrackingUnit at its <module>
 * positions. The cellID layer is 2*(station-1) + (1 front | 2 back), so it is
 * monotonic in z, and module ids restart per face.
 *
 * A <support_plate hole_radius="R"> gets its beam hole generated here: a half
 * circle on the -x side from (0, R) to (0, -R) in 15 degree steps. Its <point>
 * list continues from (0, -R) around the rest of the outline.
 *
 * A <face> lists <module> placements and <modules ref="name"/> references to a
 * <module_group name="name">, expanded in document order. Module ids follow that
 * order, so shared columns are written once and keep the same ids everywhere.
 *
 * The builder fails early, with a clear message, when an id does not fit its
 * readout field, a face is duplicated or empty, a stave reaches into the beam
 * hole of its plate, or two faces' ACTS layers would overlap in z.
 *
 * @{
 */

#include "DD4hep/DetFactoryHelper.h"
#include "DD4hep/IDDescriptor.h"
#include "DD4hep/Printout.h"
#include "DD4hep/Shapes.h"
#include "DD4hepDetectorHelper.h"
#include "DDRec/DetectorData.h"
#include "DDRec/Surface.h"
#include "XML/Utilities.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

using namespace dd4hep;
using namespace dd4hep::rec;

namespace {

struct SupportSlice {
  Volume volume;
  Position position;
};

struct SupportPlates {
  std::map<std::string, std::vector<SupportSlice>> slices; // by plate name
  std::map<std::string, double> hole_radius;               // by plate name, if generated
  double thickness = 0.0;
};

struct TrackingUnit {
  Assembly assembly;
  std::vector<PlacedVolume> sensors; // sensor id = index + 1
  std::vector<VolPlane> surfaces;
  double z_min        = +std::numeric_limits<double>::infinity(); // z extent of the whole stack
  double z_max        = -std::numeric_limits<double>::infinity();
  double sensor_z_min = +std::numeric_limits<double>::infinity(); // sensor mid-planes
  double sensor_z_max = -std::numeric_limits<double>::infinity();
  double x_min        = +std::numeric_limits<double>::infinity(); // xy footprint of the stack
  double x_max        = -std::numeric_limits<double>::infinity();
  double y_min        = +std::numeric_limits<double>::infinity();
  double y_max        = -std::numeric_limits<double>::infinity();
};

// Throws unless value fits the named field of the readout's cellID
void check_id_field(SensitiveDetector sens, const std::string& det_name, const std::string& field,
                    long value) {
  const auto* element = sens.readout().idSpec().field(field);
  // From the width, since BitFieldElement::maxValue() is 2^width for unsigned fields
  const int width = static_cast<int>(element->width());
  const long min  = element->isSigned() ? -(1L << (width - 1)) : 0L;
  const long max  = element->isSigned() ? (1L << (width - 1)) - 1 : (1L << width) - 1;
  if (value < min || value > max) {
    throw std::runtime_error(det_name + ": " + field + " id " + std::to_string(value) +
                             " does not fit the readout field " + field + ":" +
                             std::to_string(width) + " (allowed " + std::to_string(min) + ".." +
                             std::to_string(max) + ")");
  }
}

xml_h find_named(xml_det_t x_det, const xml::Strng_t& tag, const std::string& name) {
  for (xml_coll_t it(x_det, tag); it; ++it) {
    xml_comp_t x = it;
    if (x.nameStr() == name) {
      return x;
    }
  }
  return xml_h();
}

TrackingUnit build_tracking_unit(Detector& description, SensitiveDetector sens,
                                 const std::string& det_name, xml_comp_t x_unit) {
  TrackingUnit unit;
  unit.assembly = Assembly(det_name + "_TrackingUnit");
  unit.assembly.setVisAttributes(description,
                                 getAttrOrDefault<std::string>(x_unit, _Unicode(vis), ""));

  // The stack extent is needed first: it sets the sensor surface thicknesses below
  for (xml_coll_t comp(x_unit, _U(module_component)); comp; ++comp) {
    xml_comp_t x_comp   = comp;
    const double pz     = x_comp.position().z();
    xml_dim_t x_box     = x_comp.child(_U(box));
    const double half_z = x_box.z() / 2.0;
    unit.z_min          = std::min(unit.z_min, pz - half_z);
    unit.z_max          = std::max(unit.z_max, pz + half_z);
    unit.x_min          = std::min(unit.x_min, x_comp.position().x() - x_box.x() / 2.0);
    unit.x_max          = std::max(unit.x_max, x_comp.position().x() + x_box.x() / 2.0);
    unit.y_min          = std::min(unit.y_min, x_comp.position().y() - x_box.y() / 2.0);
    unit.y_max          = std::max(unit.y_max, x_comp.position().y() + x_box.y() / 2.0);
  }

  const Vector3D u(-1.0, 0.0, 0.0);
  const Vector3D v(0.0, -1.0, 0.0);
  const Vector3D n(0.0, 0.0, 1.0);
  for (xml_coll_t comp(x_unit, _U(module_component)); comp; ++comp) {
    xml_comp_t x_comp = comp;
    xml_dim_t x_box   = x_comp.child(_U(box));
    xml_dim_t x_pos   = x_comp.position();

    Volume vol(det_name + "_" + x_comp.nameStr(),
               Box(x_box.x() / 2.0, x_box.y() / 2.0, x_box.z() / 2.0),
               description.material(x_comp.materialStr()));
    vol.setVisAttributes(description, getAttrOrDefault<std::string>(x_comp, _Unicode(vis), ""));
    PlacedVolume pv = unit.assembly.placeVolume(vol, Position(x_pos.x(), x_pos.y(), x_pos.z()));

    if (x_comp.isSensitive()) {
      const int sensor_id = static_cast<int>(unit.sensors.size()) + 1;
      check_id_field(sens, det_name, "sensor", sensor_id);
      vol.setSensitiveDetector(sens);
      pv.addPhysVolID("sensor", sensor_id);
      unit.sensor_z_min = std::min(unit.sensor_z_min, x_pos.z());
      unit.sensor_z_max = std::max(unit.sensor_z_max, x_pos.z());
      unit.sensors.push_back(pv);
      // DDRec inner/outer thickness: the extent of the stave stack on either side of the
      // sensor
      unit.surfaces.emplace_back(vol, SurfaceType(SurfaceType::Sensitive), x_pos.z() - unit.z_min,
                                 unit.z_max - x_pos.z(), u, v, n);
    }
  }
  if (unit.sensors.empty()) {
    throw std::runtime_error(det_name + ": <module> has no sensitive <module_component>");
  }
  return unit;
}

// Slices are stacked upstream to downstream, centered on the plate mid-plane
SupportPlates build_support_plates(Detector& description, xml_det_t x_det) {
  SupportPlates plates;
  xml_comp_t x_stack          = x_det.child(_Unicode(support_stack));
  const std::string stack_vis = getAttrOrDefault<std::string>(x_stack, _Unicode(vis), "");
  for (xml_coll_t sl(x_stack, _U(slice)); sl; ++sl) {
    plates.thickness += xml_comp_t(sl).thickness();
  }

  for (xml_coll_t pl(x_det, _Unicode(support_plate)); pl; ++pl) {
    xml_comp_t x_plate           = pl;
    const std::string plate_name = x_plate.nameStr();
    std::vector<double> xs;
    std::vector<double> ys;
    if (x_plate.hasAttr(_Unicode(hole_radius))) {
      // Beam hole: half circle on the -x side, (0, R) -> (-R, 0) -> (0, -R)
      const double radius = x_plate.attr<double>(_Unicode(hole_radius));
      if (!(radius > 0.0)) {
        throw std::runtime_error(plate_name + ": hole_radius must be positive");
      }
      plates.hole_radius[plate_name] = radius;
      constexpr int n_steps          = 12; // 15 degree steps over 180 degrees
      for (int k = 0; k <= n_steps; ++k) {
        const double angle = k * M_PI / n_steps;
        // Exact zeros at the three cardinal points keep the outline free of 1e-15 noise
        xs.push_back((k == 0 || k == n_steps) ? 0.0 : -radius * std::sin(angle));
        ys.push_back(2 * k == n_steps ? 0.0 : radius * std::cos(angle));
      }
    }
    for (xml_coll_t point(x_plate, _U(point)); point; ++point) {
      xs.push_back(xml_comp_t(point).x());
      ys.push_back(xml_comp_t(point).y());
    }
    if (xs.size() < 3) {
      throw std::runtime_error(plate_name + ": outline has fewer than three points");
    }

    auto& slices = plates.slices[plate_name];
    double z_low = -plates.thickness / 2.0;
    for (xml_coll_t sl(x_stack, _U(slice)); sl; ++sl) {
      xml_comp_t x_slice     = sl;
      const double thickness = x_slice.thickness();
      Volume vol(plate_name + "_" + x_slice.nameStr(),
                 ExtrudedPolygon(xs, ys, {-thickness / 2.0, thickness / 2.0}, {0.0, 0.0},
                                 {0.0, 0.0}, {1.0, 1.0}),
                 description.material(x_slice.materialStr()));
      vol.setVisAttributes(description,
                           getAttrOrDefault<std::string>(x_slice, _Unicode(vis), stack_vis));
      slices.push_back({vol, Position(0.0, 0.0, z_low + thickness / 2.0)});
      z_low += thickness;
    }
  }
  return plates;
}

// The <module> placements of a face in document order, with <modules ref> expanded
std::vector<xml_comp_t> face_modules(xml_det_t x_det, const std::string& det_name,
                                     xml_comp_t x_face) {
  std::vector<xml_comp_t> modules;
  for (xml_coll_t child(x_face, _U(star)); child; ++child) {
    xml_comp_t x_child    = child;
    const std::string tag = x_child.tag();
    if (tag == "module") {
      modules.push_back(x_child);
    } else if (tag == "modules") {
      const std::string ref = x_child.attr<std::string>(_Unicode(ref));
      const xml_h x_group   = find_named(x_det, _Unicode(module_group), ref);
      if (!x_group.ptr()) {
        throw std::runtime_error(det_name + ": unknown <modules ref=\"" + ref + "\">");
      }
      for (xml_coll_t mp(x_group, _U(module)); mp; ++mp) {
        modules.push_back(mp);
      }
    } else {
      throw std::runtime_error(det_name + ": unexpected <" + tag + "> in <face>");
    }
  }
  return modules;
}

// Distance in the plate plane from the beam axis (the face origin) to the stave footprint
double footprint_distance_to_axis(const TrackingUnit& unit, const Transform3D& placement) {
  // A point, not a Position: Transform3D applies only the rotation to a vector
  const ROOT::Math::XYZPoint axis = placement.Inverse()(ROOT::Math::XYZPoint(0.0, 0.0, 0.0));
  const double dx                 = axis.x() - std::clamp(axis.x(), unit.x_min, unit.x_max);
  const double dy                 = axis.y() - std::clamp(axis.y(), unit.y_min, unit.y_max);
  return std::hypot(dx, dy);
}

} // namespace

static Ref_t create_B0Tracker(Detector& description, xml_h e, SensitiveDetector sens) {
  xml_det_t x_det            = e;
  const int det_id           = x_det.id();
  const std::string det_name = x_det.nameStr();

  DetElement sdet(det_name, det_id);
  Assembly assembly(det_name);

  Volume mother_vol  = description.pickMotherVolume(sdet);
  xml::Component pos = x_det.position();
  xml::Component rot = x_det.rotation();
  Transform3D pos_and_rot(RotationZYX(rot.z(), rot.y(), rot.x()),
                          Position(pos.x(), pos.y(), pos.z()));

  xml::setDetectorTypeFlag(x_det, sdet);
  auto& det_params = DD4hepDetectorHelper::ensureExtension<VariantParameters>(sdet);
  for (xml_coll_t bmat(x_det, _Unicode(boundary_material)); bmat; ++bmat) {
    DD4hepDetectorHelper::xmlToProtoSurfaceMaterial(bmat, det_params, "boundary_material");
  }

  // The top-level assembly is only a container, as on main; the parts carry the vis
  assembly.setAttributes(description, x_det.regionStr(), x_det.limitsStr(),
                         "InvisibleWithDaughters");
  sens.setType("tracker");

  // One stave type: a second <module> would otherwise be silently ignored
  int n_module_defs = 0;
  for (xml_coll_t m(x_det, _U(module)); m; ++m) {
    ++n_module_defs;
  }
  if (n_module_defs != 1) {
    throw std::runtime_error(det_name + ": expected exactly one <module> stave definition, found " +
                             std::to_string(n_module_defs));
  }
  const TrackingUnit unit =
      build_tracking_unit(description, sens, det_name, x_det.child(_U(module)));
  const SupportPlates plates = build_support_plates(description, x_det);

  // Plate mid-plane to module center, with the module's z_min side flush on the plate.
  // This assumes z_min is the side meant to face the plate (the side without the
  // AlSheetOuter cooling sheet); front staves are flipped so the same side faces it
  const double module_offset = plates.thickness / 2.0 - unit.z_min;

  // ACTS z extents of all faces along the station axis, to check they do not overlap
  struct FaceExtent {
    double z_lo;
    double z_hi;
    std::string name;
  };
  std::vector<FaceExtent> face_extents;

  xml_comp_t x_env          = x_det.child(_U(envelope));
  const double env_rmin_tol = x_env.attr<double>(_Unicode(rmin_tolerance));
  const double env_rmax_tol = x_env.attr<double>(_Unicode(rmax_tolerance));
  const double env_zmin_tol = x_env.attr<double>(_Unicode(zmin_tolerance));
  const double env_zmax_tol = x_env.attr<double>(_Unicode(zmax_tolerance));
  const std::string env_vis = getAttrOrDefault<std::string>(x_env, _Unicode(vis), "");

  for (xml_coll_t st(x_det, _Unicode(station)); st; ++st) {
    xml_comp_t x_station = st;
    const int station    = x_station.id();
    if (station < 1) {
      throw std::runtime_error(det_name + ": station ids start at 1");
    }
    xml_dim_t x_station_pos = x_station.position();
    const Position station_pos(x_station_pos.x(), x_station_pos.y(), x_station_pos.z());

    // The plates are passive: material mapping projects them onto the faces
    double hole_radius = 0.0; // largest generated beam hole among this station's plates
    for (xml_coll_t sup(x_station, _Unicode(support)); sup; ++sup) {
      const std::string ref = xml_comp_t(sup).attr<std::string>(_Unicode(ref));
      const auto plate_it   = plates.slices.find(ref);
      if (plate_it == plates.slices.end()) {
        throw std::runtime_error(det_name + ": unknown <support ref=\"" + ref + "\">");
      }
      if (const auto hole_it = plates.hole_radius.find(ref); hole_it != plates.hole_radius.end()) {
        hole_radius = std::max(hole_radius, hole_it->second);
      }
      for (const auto& slice : plate_it->second) {
        assembly.placeVolume(slice.volume, station_pos + slice.position);
      }
    }

    const std::string layout_ref =
        xml_comp_t(x_station.child(_Unicode(layout))).attr<std::string>(_Unicode(ref));
    const xml_h x_layout = find_named(x_det, _Unicode(module_layout), layout_ref);
    if (!x_layout.ptr()) {
      throw std::runtime_error(det_name + ": unknown <layout ref=\"" + layout_ref + "\">");
    }

    std::set<std::string> sides_seen;
    for (xml_coll_t fc(x_layout, _Unicode(face)); fc; ++fc) {
      xml_comp_t x_face      = fc;
      const std::string side = x_face.attr<std::string>(_Unicode(side));
      if (side != "front" && side != "back") {
        throw std::runtime_error(det_name + ": <face side=\"" + side +
                                 "\">, expected \"front\" or \"back\"");
      }
      if (!sides_seen.insert(side).second) {
        throw std::runtime_error(det_name + ": layout " + layout_ref + " has more than one " +
                                 side + " <face>");
      }
      const std::vector<xml_comp_t> modules = face_modules(x_det, det_name, x_face);
      if (modules.empty()) {
        throw std::runtime_error(det_name + ": " + side + " <face> of layout " + layout_ref +
                                 " has no modules");
      }
      const bool is_front = side == "front";
      const int layer_id  = 2 * (station - 1) + (is_front ? 1 : 2);
      check_id_field(sens, det_name, "layer", layer_id);
      check_id_field(sens, det_name, "module", static_cast<long>(modules.size()));

      // ACTS centers the disc layer on the face origin, so put it at the unit
      // center, midway between the sensor planes
      const Position face_pos(station_pos.x(), station_pos.y(),
                              station_pos.z() + (is_front ? -module_offset : module_offset));

      const std::string face_name = det_name + "_station" + std::to_string(station) + "_" + side;

      // The ACTS layer spans the sensor planes plus the z tolerances, centered on the face
      // origin; take the union with the actual plane positions to be safe
      const double plane_lo = is_front ? -unit.sensor_z_max : unit.sensor_z_min;
      const double plane_hi = is_front ? -unit.sensor_z_min : unit.sensor_z_max;
      const double acts_half =
          (unit.sensor_z_max - unit.sensor_z_min + env_zmin_tol + env_zmax_tol) / 2.0;
      face_extents.push_back({face_pos.z() + std::min(-acts_half, plane_lo - env_zmin_tol),
                              face_pos.z() + std::max(acts_half, plane_hi + env_zmax_tol),
                              face_name});
      Assembly face_vol(face_name);
      face_vol.setVisAttributes(description, env_vis);
      PlacedVolume face_pv = assembly.placeVolume(face_vol, face_pos);
      face_pv.addPhysVolID("layer", layer_id);

      DetElement face_de(sdet, face_name + "_P", layer_id);
      face_de.setPlacement(face_pv);

      // Front modules are flipped about y so they also sit on the plate; flipping about y
      // keeps the stave's orientation along y, so rotZ means the same on both faces
      int module_id = 1;
      for (const xml_comp_t& x_mod : modules) {
        RotationZYX mod_rot(getAttrOrDefault<double>(x_mod, _Unicode(rotZ), 0.0),
                            is_front ? M_PI : 0.0, 0.0);
        const Transform3D mod_tr(mod_rot, Position(x_mod.x(), x_mod.y(), 0.0));

        // Module positions are written out by hand, so catch a stave over the beam hole
        // (e.g. after B0TrackerPipeClearance changes) instead of building it
        if (hole_radius > 0.0) {
          constexpr double tolerance = 1.0 * dd4hep::um; // positions are given to 1 um
          const double clearance     = footprint_distance_to_axis(unit, mod_tr) - hole_radius;
          if (clearance < -tolerance) {
            throw std::runtime_error(det_name + ": module " + std::to_string(module_id) + " of " +
                                     face_name + " reaches " +
                                     std::to_string(-clearance / dd4hep::mm) +
                                     " mm into the beam hole (radius " +
                                     std::to_string(hole_radius / dd4hep::mm) + " mm)");
          }
          printout(DEBUG, det_name, "%s module %d: %.4f mm from the beam hole", face_name.c_str(),
                   module_id, clearance / dd4hep::mm);
        }

        PlacedVolume mod_pv = face_vol.placeVolume(unit.assembly, mod_tr);
        mod_pv.addPhysVolID("module", module_id);

        DetElement mod_de(face_de, _toString(module_id, "module%d"), module_id);
        mod_de.setPlacement(mod_pv);

        for (size_t i = 0; i < unit.sensors.size(); ++i) {
          const int sensor_id = static_cast<int>(i) + 1;
          DetElement sensor_de(mod_de, _toString(sensor_id, "sensor%d"), sensor_id);
          sensor_de.setPlacement(unit.sensors[i]);
          DD4hepDetectorHelper::ensureExtension<VariantParameters>(sensor_de).set<std::string>(
              "axis_definitions", "XYZ");
          volSurfaceList(sensor_de)->push_back(unit.surfaces[i]);
        }
        ++module_id;
      }
      face_vol->GetShape()->ComputeBBox();

      auto& face_params = DD4hepDetectorHelper::ensureExtension<VariantParameters>(face_de);
      face_params.set<double>("envelope_r_min", env_rmin_tol / dd4hep::mm);
      face_params.set<double>("envelope_r_max", env_rmax_tol / dd4hep::mm);
      face_params.set<double>("envelope_z_min", env_zmin_tol / dd4hep::mm);
      face_params.set<double>("envelope_z_max", env_zmax_tol / dd4hep::mm);
      for (xml_coll_t lmat(x_det, _Unicode(layer_material)); lmat; ++lmat) {
        DD4hepDetectorHelper::xmlToProtoSurfaceMaterial(lmat, face_params, "layer_material");
      }
    }
  }
  assembly->GetShape()->ComputeBBox();

  // Overlapping ACTS layers make the volume building throw or the navigation skip a layer
  std::sort(face_extents.begin(), face_extents.end(),
            [](const FaceExtent& a, const FaceExtent& b) { return a.z_lo < b.z_lo; });
  for (size_t i = 1; i < face_extents.size(); ++i) {
    const double gap = face_extents[i].z_lo - face_extents[i - 1].z_hi;
    if (gap <= 0.0) {
      throw std::runtime_error(det_name + ": the ACTS layers of " + face_extents[i - 1].name +
                               " and " + face_extents[i].name + " overlap by " +
                               std::to_string(-gap / dd4hep::mm) +
                               " mm; reduce the envelope z tolerances or separate the faces");
    }
    printout(DEBUG, det_name, "ACTS layer gap %s -> %s: %.3f mm", face_extents[i - 1].name.c_str(),
             face_extents[i].name.c_str(), gap / dd4hep::mm);
  }

  PlacedVolume pv = mother_vol.placeVolume(assembly, pos_and_rot);
  pv.addPhysVolID("system", det_id);
  sdet.setPlacement(pv);

  return sdet;
}

//@}
// clang-format off
DECLARE_DETELEMENT(ip6_B0Tracker, create_B0Tracker)
