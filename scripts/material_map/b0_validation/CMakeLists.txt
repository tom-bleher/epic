cmake_minimum_required(VERSION 3.21)
project(B0MaterialValidation LANGUAGES CXX)
find_package(DD4hep REQUIRED COMPONENTS DDCore DDRec)
find_package(Acts 47.7 REQUIRED COMPONENTS Core PluginDD4hep PluginJson PluginRoot)
add_executable(b0-material-probe b0_material_probe.cpp)
target_compile_features(b0-material-probe PRIVATE cxx_std_20)
target_link_libraries(b0-material-probe PRIVATE
  DD4hep::DDCore Acts::Core Acts::PluginDD4hep Acts::PluginJson Acts::PluginRoot)
