// SPDX-License-Identifier: LGPL-3.0-or-later
// Replay recorded geantino rays through the current DD4hep and ACTS geometry.
#include <Acts/Definitions/TrackParametrization.hpp>
#include <Acts/EventData/TrackParameters.hpp>
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/Geometry/VolumeBounds.hpp>
#include <Acts/Geometry/ApproachDescriptor.hpp>
#include <Acts/Geometry/Layer.hpp>
#include <Acts/Material/BinnedSurfaceMaterial.hpp>
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
  if (!envelope.inside(origin))
    throw std::runtime_error("Recorded ray starts outside the IP6 tracking envelope");
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
    throw std::runtime_error("Could not find ray's tracking-envelope exit");
  return exit;
}

int main(int argc, char** argv) {
  if (argc != 7) {
    std::cerr << "usage: b0-material-probe XML MAP RAYS.json OUTPUT.json MAX_ACCEPTED PAD_MM\n";
    return 2;
  }
  const double padding = std::stod(argv[argc - 1]);
  if (!std::isfinite(padding) || padding <= 0.)
    throw std::invalid_argument("Padding must be finite and positive");
  if (std::stoll(argv[5]) <= 0)
    throw std::invalid_argument("Requested sample size must be positive");
  auto detector = dd4hep::Detector::make_unique("");
  detector->fromCompact(argv[1]);
  detector->volumeManager();
  detector->apply("DD4hepVolumeManager", 0, nullptr);
  const auto gctx = Acts::GeometryContext{};
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
  std::set<const Acts::Surface*, GeometryIdLess> mapped;
  geometry->visitSurfaces(
      [&](const Acts::Surface* surface) {
        if (dynamic_cast<const Acts::BinnedSurfaceMaterial*>(surface->surfaceMaterial()))
          mapped.insert(surface);
        const auto* element = dynamic_cast<const ActsPlugins::DD4hepDetectorElement*>(
            surface->associatedDetectorElement());
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
  // ACTS44 visitSurfaces does not include layer approach surfaces.
  geometry->visitVolumes([&](const Acts::TrackingVolume* volume) {
    for (const auto& boundary : volume->boundarySurfaces()) {
      const auto* surface = &boundary->surfaceRepresentation();
      if (dynamic_cast<const Acts::BinnedSurfaceMaterial*>(surface->surfaceMaterial()))
        mapped.insert(surface);
    }
    if (volume->confinedLayers())
      for (const auto& layer : volume->confinedLayers()->arrayObjects()) {
        const auto* surface = &layer->surfaceRepresentation();
        if (dynamic_cast<const Acts::BinnedSurfaceMaterial*>(surface->surfaceMaterial()))
          mapped.insert(surface);
        if (layer->approachDescriptor())
          for (const auto* approach : layer->approachDescriptor()->containedSurfaces())
            if (dynamic_cast<const Acts::BinnedSurfaceMaterial*>(approach->surfaceMaterial()))
              mapped.insert(approach);
      }
  });
  Json preflight = {{"duplicate_detector_ids", duplicateDetectorIds}, {"layers", Json::array()}};
  preflight["mapped_surface_geometry"] = Json::array();
  for (const auto* surface : mapped) {
    const auto id = surface->geometryId();
    preflight["mapped_surface_geometry"].push_back({{"id", id.value()},
                                                    {"volume", id.volume()},
                                                    {"layer", id.layer()},
                                                    {"approach", id.approach()},
                                                    {"center_mm", position(surface->center(gctx))},
                                                    {"bounds", surface->bounds().values()}});
  }
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
          const Acts::Vector3 point = sensor->transform(gctx) * local;
          if (!layer->trackingVolume()->inside(point, 1e-7))
            ++outsideSensor;
          for (const auto* approach : approaches) {
            const Acts::Vector3 normal =
                approach->normal(gctx, approach->center(gctx), Acts::Vector3::UnitZ());
            const Acts::Vector3 projected =
                point - normal * normal.dot(point - approach->center(gctx));
            if (!layer->trackingVolume()->inside(projected, 1e-7))
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
    preflight["layers"].back()["center_mm"] = position(layer->surfaceRepresentation().center(gctx));
    preflight["layers"].back()["volume_center_mm"] = position(layer->trackingVolume()->center());
    preflight["layers"].back()["volume_bounds"] = layer->trackingVolume()->volumeBounds().values();
  }
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
      const Acts::Vector3 local = sensor->transform(gctx).inverse() * hit.position();
      hits.back()["local_mm"]   = position(local);
      hits.back()["bounds"]     = sensor->bounds().values();
      hits.back()["path"]       = dynamic_cast<const ActsPlugins::DD4hepDetectorElement*>(
                                sensor->associatedDetectorElement())
                                ->sourceElement()
                                .path();
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
    record["hits"]         = hits;
    record["intersection"] = Json::array();
    for (const auto* surface : mapped) {
      auto hit = surface->intersect(gctx, origin, direction, Acts::BoundaryTolerance::None())
                     .closestForward();
      if (!hit.isValid() || hit.pathLength() <= 0.)
        continue;
      const Acts::Vector3 hitPosition = hit.position();
      const auto& slab                = surface->surfaceMaterial()->materialSlab(hitPosition);
      const auto id                   = surface->geometryId();
      record["intersection"].push_back(
          {{"position", position(hit.position())},
           {"x0", slab.thicknessInX0() * surface->pathCorrection(gctx, hit.position(), direction)},
           {"volume", id.volume()},
           {"layer", id.layer()},
           {"approach", id.approach()}});
    }
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
