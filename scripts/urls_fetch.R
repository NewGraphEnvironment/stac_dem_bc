#!/usr/bin/env Rscript
# Fetch DEM and DSM GeoTIFF URLs from BC provincial objectstore.
#
# Queries nrs.objectstore.gov.bc.ca/gdwuts ONCE and derives all three
# listing artifacts from that single walk:
#
#   data/urls_list.txt   DEM .tif URLs (the item source)
#   data/urls_dsm.txt    DSM .tif URLs (the second asset)
#   data/dsm_groups.txt  every mapsheet-year that has a dsm/ directory
#
# The third file is not redundant. 11 mapsheet-years publish their surface
# model as .laz only, so their dsm/ directory yields no raster URLs at all --
# indistinguishable, from urls_dsm.txt alone, from a delivery that shipped no
# DSM. Recording which groups HAVE a dsm/ directory is what lets
# scripts/dsm_pair.py report those 1,211 tiles as a declared coverage gap
# instead of silently reclassifying them.
#
# Usage:
#   Rscript scripts/urls_fetch.R            # Production: fetch fresh from S3
#   Rscript scripts/urls_fetch.R --test     # Test: reuse cached listings

source("scripts/urls_listing.R")

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
  listing <- urls_listing_fetch()

  readr::write_lines(listing$dem, "data/urls_list.txt")
  readr::write_lines(listing$dsm, "data/urls_dsm.txt")
  readr::write_lines(listing$dsm_groups, "data/dsm_groups.txt")

  cat(sprintf("Fetched and saved %d DEM URLs\n", length(listing$dem)))
  cat(sprintf("Fetched and saved %d DSM URLs\n", length(listing$dsm)))
  cat(sprintf("Recorded %d mapsheet-years with a dsm/ directory\n",
              length(listing$dsm_groups)))
}
