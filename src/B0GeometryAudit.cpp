// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 ePIC Collaboration
#include <DD4hep/Alignments.h>
#include <DD4hep/DetFactoryHelper.h>
#include <DD4hep/Detector.h>
#include <DD4hep/Factories.h>
#include <DD4hep/Volumes.h>
#include <TGeoBBox.h>
#include <TGeoMatrix.h>
#include <fstream>
#include <iomanip>
#include <map>
#include <stdexcept>
#include <string>

namespace {
void sensors(dd4hep::DetElement element, std::map<std::string, int> ids,
             std::ostream& output, bool& first, std::size_t& count) {
  const auto placement = element.placement();
  if (placement.isValid()) {
    for (const auto& field : placement.volIDs()) ids[field.first] = field.second;
    const auto volume = placement.volume();
    if (volume.isSensitive()) {
      const auto* box = dynamic_cast<const TGeoBBox*>(volume.solid().ptr());
      if (!box) throw std::runtime_error("B0 audit expects a box sensor");
      const double local[3]{0, 0, 0}, axis[3]{0, 0, 1};
      double centre[3], normal[3];
      const auto& world = element.nominal().worldTransformation();
      world.LocalToMaster(local, centre); world.LocalToMasterVect(axis, normal);
      for (const char* key : {"system", "layer", "module", "sensor"})
        if (ids.count(key) == 0) throw std::runtime_error(std::string("B0 missing placement ID: ") + key);
      if (!first) output << ',';
      first = false; ++count;
      output << "{\"path\":" << std::quoted(element.path())
             << ",\"system\":" << ids.at("system") << ",\"layer\":" << ids.at("layer")
             << ",\"module\":" << ids.at("module") << ",\"sensor\":" << ids.at("sensor")
             << ",\"position_mm\":[" << centre[0] / dd4hep::mm << ',' << centre[1] / dd4hep::mm << ',' << centre[2] / dd4hep::mm
             << "],\"normal\":[" << normal[0] << ',' << normal[1] << ',' << normal[2]
             << "],\"size_mm\":[" << 2 * box->GetDX() / dd4hep::mm << ',' << 2 * box->GetDY() / dd4hep::mm
             << ',' << 2 * box->GetDZ() / dd4hep::mm << "]}";
    }
  }
  for (const auto& entry : element.children()) sensors(entry.second, ids, output, first, count);
}
long exportB0(dd4hep::Detector& description, int argc, char** argv) {
  if (argc != 1) throw std::runtime_error("Usage: -plugin epic_B0GeometryAudit output.json");
  std::ofstream output(argv[0]);
  if (!output) throw std::runtime_error("Cannot open B0 geometry audit output");
  output << std::setprecision(17) << "{\"schema_version\":1,\"producer\":\"DD4hep sensitive DetElements\",\"sensors\":[";
  bool first = true; std::size_t count = 0;
  sensors(description.detector("B0Tracker"), {}, output, first, count);
  output << "],\"sensor_count\":" << count << "}\n";
  if (!output || count == 0) throw std::runtime_error("B0 geometry audit failed or found no sensors");
  return 1;
}
}
DECLARE_APPLY(epic_B0GeometryAudit, exportB0)
