# Geometry template

The various compact detector subsystem views and configurations are based on the main `epic.xml.jinja2` compact description file template, modified to only show certain subsystems.

Configurations using `tracking.definitions_craterlake` load the published shared ACTS material map by default. A configuration can instead set the top-level `material_map` value to its map path. An optional `material_map_url` downloads that map with the usual `epic_FileLoader` cache; without a URL, the file must already exist.

The nominal `ip6_extended` configurations select the separate reduced-geometry map from the `tom-bleher/epic` release `b0-ip6-material-20260906-10f369975a72`. Its content-specific local filename avoids collisions with older maps. The 822,627-byte artifact has SHA-256 `10f369975a72c82f91b3c8bffde9ff0dcc5a524045c6d7baa4be91a6af6fdcd3`; validation and provenance accompany the release. It applies to the realistic B0 geometry, not the simplified comparison or the vacuum variant.
