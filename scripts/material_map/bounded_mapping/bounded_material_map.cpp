// SPDX-License-Identifier: LGPL-3.0-or-later
// Bound recorded straight geantino material to the ACTS tracking envelope.
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/Geometry/ApproachDescriptor.hpp>
#include <Acts/Geometry/Layer.hpp>
#include <Acts/Geometry/VolumeBounds.hpp>
#include <Acts/Material/BinnedSurfaceMaterialAccumulater.hpp>
#include <Acts/Material/IntersectionMaterialAssigner.hpp>
#include <Acts/Material/MaterialMapper.hpp>
#include <ActsPlugins/DD4hep/ConvertDD4hepDetector.hpp>
#include <ActsPlugins/Json/JsonMaterialDecorator.hpp>
#include <ActsPlugins/Root/RootMaterialTrackIo.hpp>
#include <DD4hep/Detector.h>
#include <DD4hep/VolumeManager.h>
#include <TChain.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>

using Json = nlohmann::json;

double trackingExit(const Acts::TrackingVolume& envelope, const Acts::GeometryContext& gctx,
                    const Acts::Vector3& origin, const Acts::Vector3& direction) {
  if (!envelope.inside(origin))
    throw std::runtime_error("Recorded ray starts outside tracking envelope");
  double exit = std::numeric_limits<double>::infinity();
  for (const auto& boundary : envelope.boundarySurfaces()) {
    const auto hit = boundary->surfaceRepresentation()
                         .intersect(gctx, origin, direction, Acts::BoundaryTolerance::None())
                         .closestForward();
    if (hit.isValid() && hit.pathLength() > 0. &&
        !envelope.inside(hit.position() + 1e-4 * direction))
      exit = std::min(exit, hit.pathLength());
  }
  if (!std::isfinite(exit))
    throw std::runtime_error("Cannot find first tracking-envelope exit");
  return exit;
}

int main(int argc, char** argv) try {
  if (argc != 8) {
    std::cerr << "usage: bounded-material-map XML BINNING_MAP INPUT.root OUTPUT.cbor FIRST COUNT "
                 "PAD_MM\n";
    return 2;
  }
  const long long first = std::stoll(argv[5]), count = std::stoll(argv[6]);
  const double padding = std::stod(argv[7]);
  if (!std::isfinite(padding) || padding <= 0.)
    throw std::invalid_argument("Invalid padding");
  if (std::filesystem::exists(argv[4]) || std::filesystem::exists(std::string(argv[4]) + ".json"))
    throw std::runtime_error("Output already exists");
  auto detector = dd4hep::Detector::make_unique("");
  detector->fromCompact(argv[1]);
  detector->volumeManager();
  detector->apply("DD4hepVolumeManager", 0, nullptr);
  const Acts::GeometryContext gctx{};
  const Acts::MagneticFieldContext mctx{};
  auto decorator = std::make_shared<Acts::JsonMaterialDecorator>(
      Acts::MaterialMapJsonConverter::Config{}, argv[2], Acts::Logging::WARNING);
  auto logger         = Acts::getDefaultLogger("BoundedMapping", Acts::Logging::WARNING);
  const auto geometry = ActsPlugins::convertDD4hepDetector(
      detector->world(), *logger, Acts::equidistant, Acts::equidistant, Acts::equidistant, 1.,
      padding, Acts::UnitConstants::fm, ActsPlugins::sortDetElementsByID, gctx, decorator);
  std::map<Acts::GeometryIdentifier, const Acts::Surface*> selected;
  auto select = [&](const Acts::Surface* surface) {
    if (surface->surfaceMaterial())
      selected.emplace(surface->geometryId(), surface);
  };
  geometry->visitSurfaces(select, false);
  geometry->visitVolumes([&](const Acts::TrackingVolume* volume) {
    for (const auto& boundary : volume->boundarySurfaces())
      select(&boundary->surfaceRepresentation());
    if (volume->confinedLayers())
      for (const auto& layer : volume->confinedLayers()->arrayObjects()) {
        select(&layer->surfaceRepresentation());
        if (layer->approachDescriptor())
          for (const auto* surface : layer->approachDescriptor()->containedSurfaces())
            select(surface);
      }
  });
  std::vector<const Acts::Surface*> surfaces;
  for (const auto& [id, surface] : selected)
    surfaces.push_back(surface);
  if (surfaces.empty())
    throw std::runtime_error("No material surfaces selected");
  Acts::IntersectionMaterialAssigner::Config finderConfig;
  finderConfig.surfaces = surfaces;
  Acts::BinnedSurfaceMaterialAccumulater::Config accumulatorConfig;
  accumulatorConfig.geoContext         = gctx;
  accumulatorConfig.materialSurfaces   = surfaces;
  accumulatorConfig.emptyBinCorrection = true;
  Acts::MaterialMapper::Config config;
  config.assignmentFinder = std::make_shared<Acts::IntersectionMaterialAssigner>(
      finderConfig, Acts::getDefaultLogger("Assignment", Acts::Logging::WARNING));
  config.surfaceMaterialAccumulater = std::make_shared<Acts::BinnedSurfaceMaterialAccumulater>(
      accumulatorConfig, Acts::getDefaultLogger("Accumulator", Acts::Logging::WARNING));
  Acts::MaterialMapper mapper(config, Acts::getDefaultLogger("Mapper", Acts::Logging::WARNING));
  auto state = mapper.createState();
  TChain tree("material-tracks");
  if (tree.Add(argv[3]) != 1)
    throw std::runtime_error("Cannot open input tree");
  const auto entries = tree.GetEntries();
  if (first < 0 || count <= 0 || first >= entries || count > entries - first)
    throw std::invalid_argument("Entry range outside recorded tree");
  for (const auto* name : {"event_id", "v_x", "v_y", "v_z", "v_px", "v_py", "v_pz", "mat_x",
                           "mat_y", "mat_z", "mat_dx", "mat_dy", "mat_dz", "mat_step_length",
                           "mat_X0", "mat_L0", "mat_A", "mat_Z", "mat_rho"})
    if (!tree.GetBranch(name))
      throw std::runtime_error(std::string("Missing branch ") + name);
  ActsPlugins::RootMaterialTrackIo reader({});
  reader.connectForRead(tree);
  long long clipped = 0, discarded = 0, totalSteps = 0, noSurfaceRays = 0;
  double keptX0 = 0., discardedX0 = 0., assignedX0 = 0., unassignedX0 = 0.;
  double unassignedWithoutSurfacesX0 = 0.;
  double minExit = std::numeric_limits<double>::infinity(), maxExit = 0.;
  for (long long entry = first; entry < first + count; ++entry) {
    if (tree.GetEntry(entry) <= 0)
      throw std::runtime_error("Failed reading entry");
    auto ray           = reader.read();
    const auto& origin = ray.first.first;
    if (!origin.allFinite() || !ray.first.second.allFinite() || ray.first.second.norm() <= 0.)
      throw std::runtime_error("Invalid ray");
    const Acts::Vector3 direction = ray.first.second.normalized();
    ray.first.second              = direction;
    const double exit = trackingExit(*geometry->highestTrackingVolume(), gctx, origin, direction);
    minExit           = std::min(minExit, exit);
    maxExit           = std::max(maxExit, exit);
    auto original     = std::move(ray.second.materialInteractions);
    ray.second.materialInteractions.clear();
    ray.second.materialInX0 = 0.;
    ray.second.materialInL0 = 0.;
    for (auto step : original) {
      ++totalSteps;
      const auto& material = step.materialSlab.material();
      const double length  = step.materialSlab.thickness();
      if (!step.position.allFinite() || !step.direction.allFinite() || !std::isfinite(length) ||
          length < 0. || step.direction.norm() <= 0. || !std::isfinite(material.X0()) ||
          material.X0() <= 0. || !std::isfinite(material.L0()) || material.L0() <= 0. ||
          !std::isfinite(material.Ar()) || material.Ar() <= 0. || !std::isfinite(material.Z()) ||
          material.Z() <= 0. || !std::isfinite(material.massDensity()) ||
          material.massDensity() <= 0.)
        throw std::runtime_error("Nonfinite or invalid recorded material step");
      const Acts::Vector3 stepDirection = step.direction.normalized();
      if (stepDirection.dot(direction) < 1. - 1e-6)
        throw std::runtime_error("Input is not a straight geantino trajectory");
      const double start = (step.position - origin).dot(direction);
      // ROOT stores single-precision coordinates. Permit their roundoff,
      // but reject displaced or curved trajectories instead of projecting them.
      if ((step.position - origin - start * direction).norm() >
          1e-6 * std::max(1., std::abs(start)))
        throw std::runtime_error("Recorded step is not on the initial straight ray");
      const double begin = std::max(0., start), end = std::min(exit, start + length);
      const double kept     = std::max(0., end - begin);
      const double fraction = length > 0. ? kept / length : 0.;
      keptX0 += fraction * step.materialSlab.thicknessInX0();
      discardedX0 += (1. - fraction) * step.materialSlab.thicknessInX0();
      if (kept <= 0.) {
        ++discarded;
        continue;
      }
      if (kept < length)
        ++clipped;
      step.position += (begin - start + 0.5 * kept) * stepDirection;
      step.materialSlab = Acts::MaterialSlab(material, kept);
      ray.second.materialInX0 += step.materialSlab.thicknessInX0();
      ray.second.materialInL0 += step.materialSlab.thicknessInL0();
      ray.second.materialInteractions.push_back(std::move(step));
    }
    const auto [assigned, unassigned] = mapper.mapMaterial(*state, gctx, mctx, ray);
    assignedX0 += assigned.second.materialInX0;
    unassignedX0 += unassigned.second.materialInX0;
    if (config.assignmentFinder->assignmentCandidates(gctx, mctx, origin, direction)
            .first.empty()) {
      ++noSurfaceRays;
      unassignedWithoutSurfacesX0 += unassigned.second.materialInX0;
    }
    if ((entry - first + 1) % 100000 == 0)
      std::cout << "processed=" << entry - first + 1 << std::endl;
  }
  const double residual = keptX0 - assignedX0 - unassignedX0;
  if (!std::isfinite(residual) || std::abs(residual) > 1e-6 * std::max(1., keptX0))
    throw std::runtime_error("Retained material not conserved in assignment");
  Json occupancy          = Json::array();
  const auto& accumulated = dynamic_cast<const Acts::BinnedSurfaceMaterialAccumulater::State&>(
      *state->surfaceMaterialAccumulaterState);
  for (const auto& [id, surface] : accumulated.accumulatedMaterial) {
    std::vector<unsigned> counts;
    for (const auto& row : surface.accumulatedMaterial())
      for (const auto& bin : row)
        counts.push_back(bin.totalAverage().second);
    std::sort(counts.begin(), counts.end());
    occupancy.push_back({{"geometry_id", id.value()},
                         {"volume", id.volume()},
                         {"layer", id.layer()},
                         {"approach", id.approach()},
                         {"bins", counts.size()},
                         {"unvisited_bins", std::count(counts.begin(), counts.end(), 0)},
                         {"min_tracks", counts.front()},
                         {"median_tracks", counts[counts.size() / 2]},
                         {"max_tracks", counts.back()}});
  }
  Acts::MaterialMapJsonConverter converter({}, Acts::Logging::WARNING);
  const auto cbor = Json::to_cbor(converter.materialMapsToJson(mapper.finalizeMaps(*state)));
  std::ofstream output(argv[4], std::ios::binary);
  output.exceptions(std::ios::failbit | std::ios::badbit);
  output.write(reinterpret_cast<const char*>(cbor.data()), cbor.size());
  output.close();
  std::ofstream report(std::string(argv[4]) + ".json");
  report.exceptions(std::ios::failbit | std::ios::badbit);
  report << Json({{"xml", argv[1]},
                  {"binning_map", argv[2]},
                  {"input", argv[3]},
                  {"first_entry", first},
                  {"entries", count},
                  {"input_entries", entries},
                  {"padding_mm", padding},
                  {"tracking_envelope_geometry_id",
                   geometry->highestTrackingVolume()->geometryId().value()},
                  {"tracking_envelope_bounds",
                   geometry->highestTrackingVolume()->volumeBounds().values()},
                  {"minimum_exit_path_mm", minExit},
                  {"maximum_exit_path_mm", maxExit},
                  {"total_steps", totalSteps},
                  {"clipped_steps", clipped},
                  {"discarded_steps", discarded},
                  {"rays_without_mapping_surface", noSurfaceRays},
                  {"retained_X_over_X0_sum", keptX0},
                  {"discarded_X_over_X0_sum", discardedX0},
                  {"assigned_X_over_X0_sum", assignedX0},
                  {"unassigned_X_over_X0_sum", unassignedX0},
                  {"unassigned_no_intersected_mapping_surface_X_over_X0_sum",
                   unassignedWithoutSurfacesX0},
                  {"conservation_residual", residual},
                  {"surface_bin_occupancy", occupancy},
                  {"policy", "Clip straight Geant4 pre-step segments to first "
                             "highest-tracking-volume exit; assign retained midpoints with native "
                             "ACTS intersection assignment and empty-bin correction"}})
                .dump(2)
         << '\n';
  report.close();
  std::cout << "mapped_entries=" << count << " surfaces=" << surfaces.size()
            << " conservation_residual=" << residual << std::endl;
} catch (const std::exception& error) {
  std::cerr << error.what() << '\n';
  return 1;
}
