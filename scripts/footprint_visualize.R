
library(sf)

coords <- matrix(
  c(
    -121.8036599520526, 53.89958949150124,
    -121.59934140177849, 53.89737950008847,
    -121.59588418039374, 54.00041393725738,
    -121.80070672673186, 54.00263224646965,
    -121.8036599520526, 53.89958949150124
  ),
  ncol = 2,
  byrow = TRUE
)

poly <- sf::st_sfc(
  sf::st_polygon(list(coords)),
  crs = 4326
)

poly_sf <- sf::st_sf(geometry = poly)

bbox <- c(
  xmin = -121.8036599520526,
  ymin = 53.89737950008847,
  xmax = -121.59588418039374,
  ymax = 54.00263224646965
)

# Convert bbox to matrix of corner coordinates
coords <- matrix(c(
  bbox["xmin"], bbox["ymin"],
  bbox["xmax"], bbox["ymin"],
  bbox["xmax"], bbox["ymax"],
  bbox["xmin"], bbox["ymax"],
  bbox["xmin"], bbox["ymin"]  # close the ring
), ncol = 2, byrow = TRUE)

# Create sf polygon
poly <- sf::st_polygon(list(coords)) |> sf::st_sfc(crs = 4326)
poly_bbox <- sf::st_sf(geometry = poly)

mapview::mapview(poly_sf) |> 
  mapview::mapview(poly_bbox, color = "red", lwd = 2)

# Make bbox as LINESTRING
bbox_line <- sf::st_as_sfc(sf::st_bbox(poly_bbox)) |>
  sf::st_cast("LINESTRING") |>
  sf::st_sf()


coords <- matrix(c(
  -121.72551, 54.00186,
  -121.59762, 53.94887,
  -121.59588, 54.00041,
  -121.72551, 54.00186  # closing the ring
), ncol = 2, byrow = TRUE)

# Convert to LINESTRING (not POLYGON)
line <- sf::st_linestring(coords) |> sf::st_sfc(crs = 4326)
footprint_sf <- sf::st_sf(geometry = line)
# Combine polygon and bbox outline on one map
mapview::mapview(poly_sf) +
  mapview::mapview(bbox_line, color = "red", lwd = 2) +
  mapview::mapview(footprint_sf, color = "green", lwd = 2)

#this is to do with the-----------------------------------------------------------------------------------------------------
library(sf)

# Define the coordinates as a matrix (lon, lat)
coords_wgs84 <- matrix(
  c(
    -121.40066, 53.9033,
    -121.40201, 53.86728,
    -121.23501, 53.79724,
    -121.19939, 53.7967,
    -121.19726, 53.84669,
    -121.22434, 53.85783,
    -121.29681, 53.87767,
    -121.35946, 53.90275,
    -121.40066, 53.9033
  ),
  ncol = 2,
  byrow = TRUE
)

# Create polygon in WGS84
poly_wgs84 <- sf::st_sfc(sf::st_polygon(list(coords_wgs84)), crs = 4326)

# Transform to EPSG:3157
poly_3157 <- sf::st_transform(poly_wgs84, 3157)

# Print as pretty JSON-style coordinates
coords_3157 <- sf::st_coordinates(poly_3157)[, 1:2]

# Output formatted for copy-paste
json_coords <- apply(coords_3157, 1, function(pt) {
  sprintf("          [%.1f, %.1f]", pt[1], pt[2])
})

cat('[\n', paste(json_coords, collapse = ',\n'), '\n      ]\n')

# Coordinates matrix (close the ring explicitly)
coords <- matrix(c(
  -119.40053338927621, 52.79566272471252,
  -119.19419935891520, 52.79965904570134,
  -119.19948670972303, 52.90433844541799,
  -119.40631682427218, 52.90032702306161,
  -119.40053338927621, 52.79566272471252  # closing point
), ncol = 2, byrow = TRUE)

# Create sf polygon
poly <- sf::st_polygon(list(coords)) |> sf::st_sfc(crs = 4326)
poly_sf <- sf::st_sf(geometry = poly)

# 093-093h-2019-dem-bc_093h084_xli1m_utm10_2019
wkt <- "POLYGON ((-121.40461294509775314 53.79960989157642359, 
                  -121.19939305697234033 53.79670359157857007, 
                  -121.19493664604227945 53.90038439235420498, 
                  -121.40066388155801747 53.90330168726016069, 
                  -121.40461294509775314 53.79960989157642359))"

poly_sf2 <- sf::st_as_sfc(wkt, crs = 4326) |> 
  sf::st_sf(geometry = _)

mapview::mapview(poly_sf) +
  mapview::mapview(poly_sf2)

