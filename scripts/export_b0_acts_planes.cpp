// SPDX-License-Identifier: LGPL-3.0-or-later
// Build against DD4hep::DDCore, Acts::Core, Acts::PluginDD4hep and Acts::PluginRoot.
// Usage: export_b0_acts_planes compact.xml output.json
#include "b0_acts_boundary_check.h"
#include <Acts/Surfaces/RectangleBounds.hpp>
#include <ActsPlugins/DD4hep/ConvertDD4hepDetector.hpp>
#include <ActsPlugins/DD4hep/DD4hepDetectorElement.hpp>
#include <DD4hep/Detector.h>
#include <fstream>
#include <map>

int main(int argc, char** argv) {
  if (argc != 3) throw std::runtime_error("Usage: export_b0_acts_planes compact.xml output.json");
  auto detector = dd4hep::Detector::make_unique("");
  detector->fromCompact(argv[1]);
  auto logger = Acts::getDefaultLogger("Planes", Acts::Logging::WARNING);
  auto geometry = ActsPlugins::convertDD4hepDetector(
      detector->world(), *logger, Acts::equidistant, Acts::equidistant,
      Acts::equidistant, Acts::UnitConstants::mm, 5 * Acts::UnitConstants::mm);
  auto context = Acts::GeometryContext::dangerouslyDefaultConstruct();
  // Record the plugin actually loaded by DD4hep, not a guessed install prefix.
  std::ifstream maps("/proc/self/maps");
  std::string line, library;
  while (std::getline(maps, line)) {
    const auto start = line.find('/');
    if (start != std::string::npos && line.substr(start).ends_with("/libepic.so"))
      library = line.substr(start);
  }
  if (library.empty()) throw std::runtime_error("Cannot identify loaded libepic.so in /proc/self/maps");
  std::ofstream out(argv[2]);
  out << std::setprecision(17) << "{\"geometry_library\":" << std::quoted(library)
      << ",\"compact_file\":" << std::quoted(argv[1])
      << ",\"length_unit\":\"mm\",\"layer_envelope_z_mm\":5,\"surfaces\":[";
  bool first = true;
  auto write = [&](const Acts::Surface& surface, const char* kind,
                   double a, double b, double thickness) {
    if (!first) out << ',';
    first = false;
    const auto transform = epic::geometryTransform(surface, context, 0);
    out << "{\"kind\":\"" << kind << "\",\"id\":\"" << surface.geometryId().value()
        << "\",\"layer\":" << surface.geometryId().layer() << ",\"a\":" << a
        << ",\"b\":" << b << ",\"thickness\":" << thickness << ",\"transform\":[";
    for (int row = 0; row < 3; ++row)
      for (int col = 0; col < 4; ++col)
        out << ((row || col) ? "," : "") << transform.matrix()(row, col);
    out << "]}";
  };
  std::map<std::uint64_t, const Acts::Layer*> layers;
  geometry->visitSurfaces([&](const Acts::Surface* surface) {
    const auto* layer = surface->associatedLayer();
    if (!layer || !layer->trackingVolume() ||
        layer->trackingVolume()->volumeName().find("B0Tracker") == std::string::npos) return;
    const auto* bounds = dynamic_cast<const Acts::RectangleBounds*>(&surface->bounds());
    const auto* element = dynamic_cast<const ActsPlugins::DD4hepDetectorElement*>(surface->surfacePlacement());
    if (!bounds || !element) throw std::runtime_error("Expected rectangular B0 sensor");
    write(*surface, "measurement", bounds->halfLengthX(), bounds->halfLengthY(), element->thickness());
    layers.emplace(layer->geometryId().value(), layer);
  });
  for (const auto& [identifier, layer] : layers)
    for (const auto* surface : layer->approachDescriptor()->containedSurfaces()) {
      const auto* bounds = dynamic_cast<const Acts::RadialBounds*>(&surface->bounds());
      if (bounds) write(*surface, "material approach", bounds->rMin(), bounds->rMax(), 0);
    }
  geometry->visitSurfaces([&](const Acts::Surface* surface) {
    const auto* layer = surface->associatedLayer();
    if (!layer || !layer->trackingVolume() || layer->surfaceArray() ||
        layer->trackingVolume()->volumeName().find("B0Tracker") == std::string::npos ||
        surface != &layer->surfaceRepresentation() || !surface->surfaceMaterial()) return;
    const auto* bounds = dynamic_cast<const Acts::RadialBounds*>(&surface->bounds());
    if (bounds) write(*surface, "passive material", bounds->rMin(), bounds->rMax(), layer->layerThickness());
  }, false);
  out << "]}\n";
  if (!out) throw std::runtime_error("Unable to write plane export");
}
