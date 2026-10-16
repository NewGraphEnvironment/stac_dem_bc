# Generate hex sticker for non-package repo
# Edit package_name below for this repo

if (!requireNamespace("pak", quietly = TRUE)) {
  install.packages("pak")
}

if (!requireNamespace("hexSticker", quietly = TRUE)) {
  pak::pkg_install("hexSticker")
}

library(hexSticker)

package_name <- "stac_dem_bc"

logo_url <- "https://raw.githubusercontent.com/NewGraphEnvironment/new_graphiti/main/assets/logos/logo_newgraph/WHITE/PNG/nge-icon_white.png"
logo_file <- "data-raw/nge-icon_white.png"
output_file <- "man/figures/logo.png"

if (!file.exists(logo_file)) {
  dir.create(dirname(logo_file), recursive = TRUE, showWarnings = FALSE)
  download.file(logo_url, logo_file, mode = "wb")
}

dir.create(dirname(output_file), recursive = TRUE, showWarnings = FALSE)

# hexSticker draws text through showtext, at showtext's own dpi rather than the device's:
# pin the value these PNGs were rendered with, or a session that set it first (the usual
# advice for showtext with ggsave(dpi =)) renders text that overruns the hexagon.
showtext::showtext_opts(dpi = 96)

# Scale font size by name length
p_size <- if (nchar(package_name) <= 3) 24 else if (nchar(package_name) <= 6) 18 else if (nchar(package_name) <= 10) 14 else 10

sticker(
  subplot = logo_file,
  package = package_name,
  s_x = 1, s_y = 1.15,
  s_width = 0.45, s_height = 0.45,
  p_size = p_size,
  p_x = 1, p_y = 0.50,
  p_color = "white",
  p_family = "Helvetica",
  h_fill = "black",
  h_color = "white",
  h_size = 1.2,
  filename = output_file,
  dpi = 300
)

message(package_name, " hex sticker -> ", output_file)

# Smaller version
# showtext draws text at its fixed dpi (above), not the device's, so the same p_size at
# dpi 150 is twice as large against the hexagon and runs into the icon: halve it with the
# dpi, and keep the large layout.
sticker(
  subplot = logo_file,
  package = package_name,
  s_x = 1, s_y = 1.15,
  s_width = 0.45, s_height = 0.45,
  p_size = p_size * 150 / 300,
  p_x = 1, p_y = 0.50,
  p_color = "white",
  p_family = "Helvetica",
  h_fill = "black",
  h_color = "white",
  h_size = 1.2,
  filename = "man/figures/logo_small.png",
  dpi = 150
)

message(package_name, " small logo -> man/figures/logo_small.png")
