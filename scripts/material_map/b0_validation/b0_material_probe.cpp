// SPDX-License-Identifier: LGPL-3.0-or-later
// Replay recorded geantino rays through the current DD4hep and ACTS geometry.
#include <Acts/Definitions/TrackParametrization.hpp>
#include <Acts/EventData/BoundTrackParameters.hpp>
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/Geometry/ApproachDescriptor.hpp>
#include <Acts/Geometry/Layer.hpp>
#include <Acts/Material/BinnedSurfaceMaterial.hpp>
#include <Acts/Material/BinnedSurfaceMaterialAccumulator.hpp>
#include <Acts/Material/IntersectionMaterialAssigner.hpp>
#include <Acts/Material/MaterialMapper.hpp>
#include <Acts/Material/MaterialValidator.hpp>
#include <Acts/Propagator/MaterialInteractor.hpp>
#include <Acts/Propagator/Navigator.hpp>
#include <Acts/Propagator/Propagator.hpp>
#include <Acts/Propagator/StandardAborters.hpp>
#include <Acts/Propagator/StraightLineStepper.hpp>
#include <Acts/Propagator/detail/SteppingLogger.hpp>
#include <Acts/Surfaces/DiscSurface.hpp>
#include <Acts/Surfaces/PlanarBounds.hpp>
#include <ActsPlugins/DD4hep/ConvertDD4hepDetector.hpp>
#include <ActsPlugins/DD4hep/DD4hepDetectorElement.hpp>
#include <ActsPlugins/Json/JsonMaterialDecorator.hpp>
#include <ActsPlugins/Root/RootMaterialTrackIo.hpp>
#include <DD4hep/Detector.h>
#include <DD4hep/VolumeManager.h>
#include <TGeoManager.h>
#include <TGeoMaterial.h>
#include <TGeoNode.h>
#include <TGeoVolume.h>
#include <TFile.h>
#include <TTree.h>
#include <nlohmann/json.hpp>

#include <fstream>
#include <iostream>
#include <regex>
#include <set>

using Json = nlohmann::json;

struct GeometryIdLess {
  template <typename T> bool operator()(const T* a, const T* b) const {
    return a->geometryId() < b->geometryId();
  }
};

Json position(const Acts::Vector3& p) { return {p.x(), p.y(), p.z()}; }

Json materialSteps(const Acts::RecordedMaterial& material) {
  Json result = Json::array();
  for (const auto& step : material.materialInteractions) {
    const auto id = step.surface ? step.surface->geometryId() : step.intersectionID;
    result.push_back({{"position", position(step.position)},
                      {"x0", step.materialSlab.thicknessInX0()},
                      {"volume", id.volume()},
                      {"layer", id.layer()},
                      {"approach", id.approach()}});
  }
  return result;
}

// ROOT/DD4hep length units are converted explicitly, independently of ACTS.
Json geometrySteps(TGeoManager& manager, const Acts::Vector3& origin,
                   const Acts::Vector3& direction) {
  const double unit = dd4hep::mm;
  double p[3]       = {origin.x() * unit, origin.y() * unit, origin.z() * unit};
  double d[3]       = {direction.x(), direction.y(), direction.z()};
  manager.InitTrack(p, d);
  Json result = Json::array();
  for (unsigned steps = 0; steps < 10000 && !manager.IsOutside(); ++steps) {
    const auto* node = manager.GetCurrentNode();
    if (!node)
      break;
    const auto* mat        = node->GetVolume()->GetMaterial();
    const double x0        = mat->GetRadLen();
    const double z0        = manager.GetCurrentPoint()[2] / unit;
    const std::string path = manager.GetPath();
    manager.FindNextBoundaryAndStep(100000. * unit);
    const double length = manager.GetStep();
    const double z1     = manager.GetCurrentPoint()[2] / unit;
    if (length > 0. && x0 > 0. && mat->GetDensity() > 1e-12) {
      result.push_back({{"z0", z0},
                        {"z1", z1},
                        {"x0", length / x0},
                        {"material", mat->GetName()},
                        {"path", path}});
    }
    if (steps == 9999)
      throw std::runtime_error("TGeo ray exceeded step limit");
  }
  return result;
}

double trackingExit(const Acts::TrackingVolume& envelope, const Acts::GeometryContext& gctx,
                    const Acts::Vector3& origin, const Acts::Vector3& direction) {
  if (!envelope.inside(gctx, origin))
    throw std::runtime_error("Recorded ray starts outside the IP6 tracking envelope");
  double exit = std::numeric_limits<double>::infinity();
  for (const auto& boundary : envelope.boundarySurfaces()) {
    const auto hit = boundary->surfaceRepresentation()
                         .intersect(gctx, origin, direction, Acts::BoundaryTolerance::None())
                         .closestForward();
    if (hit.isValid() && hit.pathLength() > 0. &&
        !envelope.inside(gctx, hit.position() + 1e-4 * direction))
      exit = std::min(exit, hit.pathLength());
  }
  if (!std::isfinite(exit))
    throw std::runtime_error("Could not find ray's tracking-envelope exit");
  return exit;
}

// The highest IP6 tracking volume contains the IP, upstream beamline material,
// and every B0 layer. Its exit bounds this map's physical domain; the last
// measurement plane does not. ROOT input positions are Geant4 pre-step points.
void remap(const Acts::TrackingGeometry& geometry,
           const std::vector<const Acts::Surface*>& surfaces, const Acts::GeometryContext& gctx,
           const Acts::MagneticFieldContext& mctx, const char* input, const char* output,
           long long first, long long count) {
  Acts::IntersectionMaterialAssigner::Config finderConfig;
  finderConfig.surfaces = surfaces;
  Acts::BinnedSurfaceMaterialAccumulator::Config accumulatorConfig;
  accumulatorConfig.materialSurfaces = surfaces;
  Acts::MaterialMapper::Config config;
  config.assignmentFinder = std::make_shared<Acts::IntersectionMaterialAssigner>(
      finderConfig, Acts::getDefaultLogger("Assignment", Acts::Logging::WARNING));
  config.surfaceMaterialAccumulator = std::make_shared<Acts::BinnedSurfaceMaterialAccumulator>(
      accumulatorConfig, Acts::getDefaultLogger("Accumulator", Acts::Logging::WARNING));
  Acts::MaterialMapper mapper(config, Acts::getDefaultLogger("Mapper", Acts::Logging::WARNING));
  auto state           = mapper.createState(gctx);
  const auto* envelope = geometry.highestTrackingVolume();
  Acts::MaterialMapper::Options options;
  options.assignmentOptions.globalVetos.push_back(
      [&](const auto& step) { return !envelope->inside(gctx, step.position, 1e-5); });
  TFile source(input, "READ");
  auto* tree = source.Get<TTree>("material-tracks");
  if (!tree)
    throw std::runtime_error("Missing material-tracks tree");
  if (first < 0 || count <= 0 || first >= tree->GetEntries() || count > tree->GetEntries() - first)
    throw std::invalid_argument("Mapping entry range is outside the recorded tree");
  for (const auto* name : {"event_id", "v_x", "v_y", "v_z", "v_px", "v_py", "v_pz", "mat_x",
                           "mat_y", "mat_z", "mat_dx", "mat_dy", "mat_dz", "mat_step_length",
                           "mat_X0", "mat_L0", "mat_A", "mat_Z", "mat_rho"})
    if (!tree->GetBranch(name))
      throw std::runtime_error(std::string("Missing required branch ") + name);
  // ACTS47 unconditionally connects these optional composition branches. Older
  // recordings omit them; RootMaterialTrackIo::read explicitly guards the empty
  // vectors and still constructs material from X0/L0/A/Z/mass density.
  const bool hasComposition = tree->GetBranch("elements") && tree->GetBranch("fraction");
  ActsPlugins::RootMaterialTrackIo reader({});
  reader.connectForRead(*tree);
  long long processed = 0, clippedSteps = 0, discardedSteps = 0;
  double keptX0 = 0., discardedX0 = 0., assignedX0 = 0., unassignedX0 = 0.;
  double unassignedWithoutSurfacesX0 = 0.;
  const auto stop                    = std::min(tree->GetEntries(), first + count);
  for (long long entry = first; entry < stop; ++entry) {
    tree->GetEntry(entry);
    auto ray             = reader.read();
    const auto& origin   = ray.first.first;
    const auto direction = ray.first.second.normalized().eval();
    const double exit    = trackingExit(*envelope, gctx, origin, direction);
    auto original        = std::move(ray.second.materialInteractions);
    ray.second.materialInteractions.clear();
    for (auto step : original) {
      const double start     = (step.position - origin).dot(direction);
      const double length    = step.materialSlab.thickness();
      const auto& properties = step.materialSlab.material();
      if (!std::isfinite(properties.X0()) || properties.X0() <= 0. ||
          !std::isfinite(properties.L0()) || properties.L0() <= 0. ||
          !std::isfinite(properties.Ar()) || properties.Ar() <= 0. ||
          !std::isfinite(properties.Z()) || properties.Z() <= 0. ||
          !std::isfinite(properties.massDensity()) || properties.massDensity() <= 0.)
        throw std::runtime_error("Invalid recorded material properties");
      const double kept     = std::clamp(exit - start, 0., length);
      const double fraction = length > 0. ? kept / length : 0.;
      keptX0 += fraction * step.materialSlab.thicknessInX0();
      discardedX0 += (1. - fraction) * step.materialSlab.thicknessInX0();
      if (kept <= 0.) {
        ++discardedSteps;
        continue;
      }
      if (kept < length)
        ++clippedSteps;
      step.materialSlab = Acts::MaterialSlab(step.materialSlab.material(), kept);
      ray.second.materialInteractions.push_back(std::move(step));
    }
    const auto [assigned, unassigned] = mapper.mapMaterial(*state, gctx, mctx, ray, options);
    assignedX0 += assigned.second.materialInX0;
    unassignedX0 += unassigned.second.materialInX0;
    if (unassigned.second.materialInX0 > 0. &&
        config.assignmentFinder->assignmentCandidates(gctx, mctx, origin, direction).first.empty())
      unassignedWithoutSurfacesX0 += unassigned.second.materialInX0;
    ++processed;
    if (processed % 100000 == 0)
      std::cout << "processed=" << processed << std::endl;
  }
  Acts::MaterialMapJsonConverter converter({}, Acts::Logging::WARNING);
  const double conservationResidual = keptX0 - assignedX0 - unassignedX0;
  if (std::abs(conservationResidual) > 1e-6 * std::max(1., keptX0))
    throw std::runtime_error("Retained material is not conserved in assignment");
  const auto map  = converter.materialMapsToJson(mapper.finalizeMaps(*state, gctx));
  const auto cbor = Json::to_cbor(map);
  std::ofstream destination(output, std::ios::binary);
  destination.exceptions(std::ios::failbit | std::ios::badbit);
  destination.write(reinterpret_cast<const char*>(cbor.data()), cbor.size());
  destination.flush();
  std::ofstream report(std::string(output) + ".json");
  report.exceptions(std::ios::failbit | std::ios::badbit);
  report << Json({{"first_entry", first},
                  {"entries", processed},
                  {"clipped_steps", clippedSteps},
                  {"discarded_steps", discardedSteps},
                  {"retained_X_over_X0_sum", keptX0},
                  {"discarded_X_over_X0_sum", discardedX0},
                  {"assigned_X_over_X0_sum", assignedX0},
                  {"unassigned_X_over_X0_sum", unassignedX0},
                  {"unassigned_no_intersected_mapping_surface_X_over_X0_sum",
                   unassignedWithoutSurfacesX0},
                  {"conservation_residual", conservationResidual},
                  {"composition_branches_present", hasComposition},
                  {"policy",
                   "Retain Geant4 steps through first exit from highest IP6 tracking volume; clip "
                   "crossing step; native ACTS47 assignment and empty-bin correction"}})
                .dump(2)
         << '\n';
  report.flush();
}

int main(int argc, char** argv) {
  if (argc != 7 && argc != 8) {
    std::cerr << "usage: b0-material-probe XML MAP RAYS.json OUTPUT.json MAX_ACCEPTED PAD_MM\n";
    std::cerr << "   or: b0-material-probe XML BINNING_MAP INPUT.root OUTPUT.cbor FIRST_ENTRY "
                 "COUNT PAD_MM\n";
    return 2;
  }
  const double padding = std::stod(argv[argc - 1]);
  if (!std::isfinite(padding) || padding <= 0.)
    throw std::invalid_argument("Padding must be finite and positive");
  if (argc == 7 && std::stoll(argv[5]) <= 0)
    throw std::invalid_argument("Requested sample size must be positive");
  auto detector = dd4hep::Detector::make_unique("");
  detector->fromCompact(argv[1]);
  detector->volumeManager();
  detector->apply("DD4hepVolumeManager", 0, nullptr);
  const auto gctx = Acts::GeometryContext::dangerouslyDefaultConstruct();
  const Acts::MagneticFieldContext mctx{};
  auto material = std::make_shared<Acts::JsonMaterialDecorator>(
      Acts::MaterialMapJsonConverter::Config{}, argv[2], Acts::Logging::WARNING);
  auto logger = Acts::getDefaultLogger("B0MaterialValidation", Acts::Logging::WARNING);
  std::shared_ptr<const Acts::TrackingGeometry> geometry = ActsPlugins::convertDD4hepDetector(
      detector->world(), *logger, Acts::equidistant, Acts::equidistant, Acts::equidistant, 1.,
      padding, Acts::UnitConstants::fm, ActsPlugins::sortDetElementsByID, gctx, material);
  std::vector<std::pair<const Acts::Surface*, int>> sensors;
  std::set<unsigned long long> detectorIds;
  unsigned duplicateDetectorIds = 0;
  Acts::IntersectionMaterialAssigner::Config assignerConfig;
  std::set<const Acts::Surface*, GeometryIdLess> mapped;
  geometry->visitSurfaces(
      [&](const Acts::Surface* surface) {
        if (dynamic_cast<const Acts::BinnedSurfaceMaterial*>(surface->surfaceMaterial()))
          mapped.insert(surface);
        const auto* element =
            dynamic_cast<const ActsPlugins::DD4hepDetectorElement*>(surface->surfacePlacement());
        if (!element)
          return;
        const std::string path = element->sourceElement().path();
        std::smatch station;
        if (std::regex_search(path, station, std::regex("/B0Tracker_layer([0-9]+)_"))) {
          sensors.emplace_back(surface, std::stoi(station[1]));
          if (!detectorIds.insert(element->identifier()).second)
            ++duplicateDetectorIds;
        }
      },
      false);
  Json preflight = {{"duplicate_detector_ids", duplicateDetectorIds}, {"layers", Json::array()}};
  std::map<const Acts::Layer*, std::vector<const Acts::Surface*>, GeometryIdLess> layers;
  for (const auto& [sensor, station] : sensors)
    layers[sensor->associatedLayer()].push_back(sensor);
  for (const auto& [layer, layerSensors] : layers) {
    unsigned outsideSensor = 0, outsideApproach = 0, mappedDiscs = 0, discs = 0;
    std::vector<const Acts::Surface*> approaches;
    for (const auto* approach : layer->approachDescriptor()->containedSurfaces()) {
      if (!dynamic_cast<const Acts::DiscSurface*>(approach))
        continue;
      ++discs;
      if (mapped.contains(approach))
        ++mappedDiscs;
      approaches.push_back(approach);
    }
    for (const auto* sensor : layerSensors) {
      const auto vertices = dynamic_cast<const Acts::PlanarBounds&>(sensor->bounds()).vertices();
      if (vertices.size() != 4)
        throw std::runtime_error("B0 containment sampling requires quadrilateral sensor bounds");
      for (int ix = 0; ix < 9; ++ix)
        for (int iy = 0; iy < 9; ++iy) {
          const double u = ix / 8., v = iy / 8.;
          const Acts::Vector2 xy = (1 - u) * (1 - v) * vertices[0] + u * (1 - v) * vertices[1] +
                                   u * v * vertices[2] + (1 - u) * v * vertices[3];
          const Acts::Vector3 local(xy.x(), xy.y(), 0.);
          const Acts::Vector3 point = sensor->localToGlobalTransform(gctx) * local;
          if (!layer->trackingVolume()->inside(gctx, point, 1e-7))
            ++outsideSensor;
          for (const auto* approach : approaches) {
            const Acts::Vector3 normal =
                approach->normal(gctx, approach->center(gctx), Acts::Vector3::UnitZ());
            const Acts::Vector3 projected =
                point - normal * normal.dot(point - approach->center(gctx));
            if (!layer->trackingVolume()->inside(gctx, projected, 1e-7))
              ++outsideApproach;
          }
        }
    }
    preflight["layers"].push_back({{"volume", layer->geometryId().volume()},
                                   {"layer", layer->geometryId().layer()},
                                   {"sensors", layerSensors.size()},
                                   {"planar_approaches", discs},
                                   {"mapped_planar_approaches", mappedDiscs},
                                   {"sensor_samples_outside", outsideSensor},
                                   {"approach_samples_outside", outsideApproach}});
  }
  assignerConfig.surfaces.assign(mapped.begin(), mapped.end());
  if (argc == 8) {
    remap(*geometry, assignerConfig.surfaces, gctx, mctx, argv[3], argv[4], std::stoll(argv[5]),
          std::stoll(argv[6]));
    return 0;
  }
  Acts::MaterialValidator::Config validatorConfig;
  validatorConfig.materialAssigner = std::make_shared<Acts::IntersectionMaterialAssigner>(
      assignerConfig, Acts::getDefaultLogger("Intersection", Acts::Logging::WARNING));
  Acts::MaterialValidator validator(
      validatorConfig, Acts::getDefaultLogger("MaterialValidator", Acts::Logging::WARNING));
  Acts::Navigator::Config navConfig{geometry};
  using Propagator = Acts::Propagator<Acts::StraightLineStepper, Acts::Navigator>;
  Propagator propagator{Acts::StraightLineStepper{}, Acts::Navigator(navConfig)};
  using Actors  = Acts::ActorList<Acts::detail::SteppingLogger, Acts::MaterialInteractor,
                                  Acts::EndOfWorldReached>;
  using Options = Propagator::Options<Actors>;

  std::ifstream raysFile(argv[3]);
  Json rays = Json::parse(raysFile), result = Json::array();
  const auto maxAccepted = std::stoul(argv[5]);
  std::size_t examined   = 0;
  for (const auto& ray : rays) {
    ++examined;
    const Acts::Vector3 origin(ray["origin"][0], ray["origin"][1], ray["origin"][2]);
    const Acts::Vector3 direction =
        Acts::Vector3(ray["direction"][0], ray["direction"][1], ray["direction"][2]).normalized();
    Json hits = Json::array();
    std::set<int> stations;
    for (const auto& [sensor, station] : sensors) {
      auto hit = sensor->intersect(gctx, origin, direction, Acts::BoundaryTolerance::None())
                     .closestForward();
      if (!hit.isValid() || hit.pathLength() <= 0.)
        continue;
      stations.insert(station);
      hits.push_back({{"position", position(hit.position())},
                      {"station", station},
                      {"id", sensor->geometryId().value()}});
    }
    if (stations.size() < 3)
      continue;
    std::sort(hits.begin(), hits.end(), [](const auto& a, const auto& b) {
      return a["position"][2].template get<double>() < b["position"][2].template get<double>();
    });
    Json record = ray;
    record["envelope_exit_z"] =
        (origin +
         direction * trackingExit(*geometry->highestTrackingVolume(), gctx, origin, direction))
            .z();
    record["hits"] = hits;
    record["intersection"] =
        materialSteps(validator.recordMaterial(gctx, mctx, origin, direction).second);
    record["tgeo"]  = geometrySteps(detector->manager(), origin, direction);
    auto parameters = Acts::BoundTrackParameters::createCurvilinear(
        Acts::Vector4(origin.x(), origin.y(), origin.z(), 0.), direction, 1. / 41., std::nullopt,
        Acts::ParticleHypothesis::proton());
    Options options(gctx, mctx);
    options.pathLimit             = 100000.;
    options.maxSteps              = 10000;
    auto& interactor              = options.actorList.get<Acts::MaterialInteractor>();
    interactor.multipleScattering = false;
    interactor.energyLoss         = false;
    interactor.recordInteractions = true;
    auto propagation              = propagator.propagate(parameters, options);
    if (!propagation.ok()) {
      record["navigation_error"] = propagation.error().message();
    } else {
      record["navigation"] =
          materialSteps(propagation->get<Acts::MaterialInteractor::result_type>());
      record["navigated_sensor_ids"] = Json::array();
      for (const auto& step : propagation->get<Acts::detail::SteppingLogger::result_type>().steps)
        if (step.surface && step.surface->geometryId().sensitive())
          record["navigated_sensor_ids"].push_back(step.surface->geometryId().value());
    }
    result.push_back(std::move(record));
    if (result.size() >= maxAccepted)
      break;
  }
  std::ofstream output(argv[4]);
  output.exceptions(std::ios::failbit | std::ios::badbit);
  output << Json({{"preflight", preflight},
                  {"mapped_surfaces", mapped.size()},
                  {"sensors", sensors.size()},
                  {"examined", examined},
                  {"rays", result}})
                .dump(2)
         << '\n';
  output.flush();
  std::cout << "accepted=" << result.size() << " examined=" << examined
            << " sensors=" << sensors.size() << " mapped_surfaces=" << mapped.size() << '\n';
  return result.empty() ? 1 : 0;
}
