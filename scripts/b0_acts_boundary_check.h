// SPDX-License-Identifier: LGPL-3.0-or-later
#pragma once

#include <Acts/Geometry/ApproachDescriptor.hpp>
#include <Acts/Geometry/CylinderVolumeBounds.hpp>
#include <Acts/Geometry/Layer.hpp>
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/Geometry/TrackingVolume.hpp>
#include <Acts/Surfaces/RadialBounds.hpp>
#include <Acts/Surfaces/DiscSurface.hpp>
#include <cmath>
#include <iomanip>
#include <ostream>
#include <set>
#include <stdexcept>
#include <string>

namespace epic {
// ACTS renamed transform() in v47. Support both installed APIs.
template <typename T>
auto geometryTransform(const T& object, const Acts::GeometryContext& context, int)
    -> decltype(object.localToGlobalTransform(context)) {
  return object.localToGlobalTransform(context);
}
template <typename T>
auto geometryTransform(const T& object, const Acts::GeometryContext& context, long)
    -> decltype(object.transform(context)) {
  return object.transform(context);
}
template <typename T>
auto geometryTransform(const T& object, const Acts::GeometryContext&, ...)
    -> decltype(object.transform()) {
  return object.transform();
}
inline void writeB0ApproachClearances(const Acts::TrackingGeometry& geometry,
                                      const Acts::GeometryContext& context, std::ostream& out) {
  const auto byIdentifier = [](const Acts::Layer* left, const Acts::Layer* right) {
    return left->geometryId().value() < right->geometryId().value();
  };
  std::set<const Acts::Layer*, decltype(byIdentifier)> layers(byIdentifier);
  geometry.visitSurfaces([&](const Acts::Surface* sensor) {
    const auto* layer = sensor->associatedLayer();
    if (layer && layer->trackingVolume() &&
        layer->trackingVolume()->volumeName().find("B0Tracker") != std::string::npos)
      layers.insert(layer);
  });
  for (const auto* layer : layers) {
    const auto* volume   = layer->trackingVolume();
    const auto* cylinder = dynamic_cast<const Acts::CylinderVolumeBounds*>(&volume->volumeBounds());
    if (!cylinder || !layer->approachDescriptor())
      throw std::runtime_error("Unsupported B0 volume or missing approach descriptor");
    for (const auto* surface : layer->approachDescriptor()->containedSurfaces()) {
      // Disc layers also expose a cylindrical approach at their radial edge.
      // This check addresses the two planar faces and their axial boundaries.
      if (!dynamic_cast<const Acts::DiscSurface*>(surface))
        continue;
      const auto* disc = dynamic_cast<const Acts::RadialBounds*>(&surface->bounds());
      if (!disc || !disc->coversFullAzimuth())
        throw std::runtime_error("B0 axial clearance requires full radial approach discs");
      const Acts::Transform3 transform = geometryTransform(*volume, context, 0).inverse() *
                                         geometryTransform(*surface, context, 0);
      // Exact extrema of the transformed disc in the owning cylinder's frame.
      const double extent =
          disc->rMax() * std::hypot(transform.linear()(2, 0), transform.linear()(2, 1));
      const double halfZ   = cylinder->get(Acts::CylinderVolumeBounds::eHalfLengthZ);
      const double centerZ = transform.translation().z();
      out << std::setprecision(17) << "B0_APPROACH_CLEARANCE " << surface->geometryId().value()
          << " " << halfZ + centerZ - extent << " " << halfZ - centerZ - extent << "\n";
    }
  }
}
} // namespace epic
