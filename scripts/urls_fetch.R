#!/usr/bin/env Rscript
# Fetch DEM GeoTIFF URLs from BC provincial objectstore.
#
# Queries nrs.objectstore.gov.bc.ca/gdwuts for DEM .tif files,
# filters out filenames with parentheses (all fail validation),
# and writes the clean URL list to data/urls_list.txt.
#
# Usage:
#   Rscript scripts/urls_fetch.R            # Production: fetch fresh from S3
#   Rscript scripts/urls_fetch.R --test     # Test: reuse cached urls_list.txt

# Parse arguments
args <- commandArgs(trailingOnly = TRUE)
test_only <- "--test" %in% args

cat(sprintf("Mode: %s\n", if (test_only) "TEST" else "PRODUCTION"))

fs::dir_create("data")

if (test_only && file.exists("data/urls_list.txt")) {
  cat("Test mode: Reusing existing data/urls_list.txt\n")
  keys_clean <- readr::read_lines("data/urls_list.txt")
  cat(sprintf("Loaded %d URLs from cache\n", length(keys_clean)))
} else {
  cat("Fetching fresh keys from BC objectstore...\n")
  keys <- ngr::ngr_s3_keys_get(
    url_bucket = "https://nrs.objectstore.gov.bc.ca/gdwuts",
    prefix = "",
    pattern = c("dem", "*.tif")
  )

  # Remove paths with ( in them (all fail validation - see issue #8)
  keys_clean <- keys[!stringr::str_detect(keys, "\\(")]

  readr::write_lines(keys_clean, "data/urls_list.txt")
  cat(sprintf("Fetched and saved %d URLs (excluded %d with parentheses)\n",
              length(keys_clean), length(keys) - length(keys_clean)))
}
