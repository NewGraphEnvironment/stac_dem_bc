#!/usr/bin/env Rscript
# Change Detection: Compare BC DEM objectstore with cached URL list
# Outputs: urls_new.txt, urls_deleted.txt
# Updates: urls_list.txt
# Exit: 0 if no changes, 1 if changes detected

library(ngr)

# Setup logging
timestamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
log_file <- sprintf("logs/%s_change_detection.log", timestamp)
sink(log_file, split = TRUE)

cat("=== STAC DEM BC Change Detection ===\n")
cat(sprintf("Started: %s\n\n", Sys.time()))

# Paths
url_bucket <- "https://nrs.objectstore.gov.bc.ca/gdwuts"
cache_file <- "data/urls_list.txt"
new_file <- "data/urls_new.txt"
deleted_file <- "data/urls_deleted.txt"

# Step 1: Fetch fresh URL list from objectstore
cat("Fetching fresh URL list from BC objectstore...\n")
cat(sprintf("  Bucket: %s\n", url_bucket))
cat(sprintf("  Pattern: dem + *.tif\n\n"))

start_time <- Sys.time()

fresh_urls <- ngr::ngr_s3_keys_get(
  url_bucket = url_bucket,
  prefix = "",
  pattern = c("dem", "*.tif")
)

fetch_time <- as.numeric(difftime(Sys.time(), start_time, units = "secs"))

cat(sprintf("Fetched %d URLs in %.1f seconds (%.1f URLs/sec)\n\n",
            length(fresh_urls), fetch_time, length(fresh_urls) / fetch_time))

# Step 2: Load cached URL list
if (file.exists(cache_file)) {
  cat(sprintf("Loading cached URL list from %s...\n", cache_file))
  cached_urls <- readr::read_lines(cache_file)
  cat(sprintf("Cached: %d URLs\n\n", length(cached_urls)))
} else {
  cat(sprintf("No cache found at %s - treating all URLs as new\n\n", cache_file))
  cached_urls <- character(0)
}

# Step 3: Compare to find changes
cat("Comparing cached vs. fresh URLs...\n")

# Convert to character vectors for comparison
fresh_set <- as.character(fresh_urls)
cached_set <- as.character(cached_urls)

# Find new and deleted URLs
new_urls <- setdiff(fresh_set, cached_set)
deleted_urls <- setdiff(cached_set, fresh_set)

cat(sprintf("  New URLs: %d\n", length(new_urls)))
cat(sprintf("  Deleted URLs: %d\n", length(deleted_urls)))
cat(sprintf("  Unchanged URLs: %d\n\n", length(intersect(fresh_set, cached_set))))

# Step 4: Write output files
fs::dir_create("data")

if (length(new_urls) > 0) {
  cat(sprintf("Writing new URLs to %s...\n", new_file))
  readr::write_lines(new_urls, new_file)
  cat(sprintf("  Wrote %d URLs\n", length(new_urls)))
} else {
  cat("No new URLs - not creating urls_new.txt\n")
  if (file.exists(new_file)) {
    file.remove(new_file)
    cat("  Removed old urls_new.txt\n")
  }
}

if (length(deleted_urls) > 0) {
  cat(sprintf("Writing deleted URLs to %s...\n", deleted_file))
  readr::write_lines(deleted_urls, deleted_file)
  cat(sprintf("  Wrote %d URLs\n", length(deleted_urls)))
} else {
  cat("No deleted URLs - not creating urls_deleted.txt\n")
  if (file.exists(deleted_file)) {
    file.remove(deleted_file)
    cat("  Removed old urls_deleted.txt\n")
  }
}

# Step 5: Update cache with fresh URLs
cat(sprintf("\nUpdating cache (%s) with fresh URL list...\n", cache_file))
readr::write_lines(fresh_set, cache_file)
cat(sprintf("  Wrote %d URLs to cache\n", length(fresh_set)))

# Summary
cat("\n=== SUMMARY ===\n")
cat(sprintf("Fresh URLs: %d\n", length(fresh_set)))
cat(sprintf("Cached URLs: %d\n", length(cached_set)))
cat(sprintf("New: %d\n", length(new_urls)))
cat(sprintf("Deleted: %d\n", length(deleted_urls)))
cat(sprintf("Total changes: %d\n", length(new_urls) + length(deleted_urls)))

# Show sample of new URLs if any
if (length(new_urls) > 0) {
  cat("\nSample new URLs (first 5):\n")
  for (url in head(new_urls, 5)) {
    cat(sprintf("  %s\n", url))
  }
  if (length(new_urls) > 5) {
    cat(sprintf("  ... and %d more\n", length(new_urls) - 5))
  }
}

cat(sprintf("\nCompleted: %s\n", Sys.time()))
cat(sprintf("Log saved to: %s\n", log_file))

sink()

# Exit code: 0 if no changes, 1 if changes detected
changes_detected <- length(new_urls) > 0 || length(deleted_urls) > 0

if (changes_detected) {
  cat("Changes detected - exit code 1\n")
  quit(status = 1)
} else {
  cat("No changes - exit code 0\n")
  quit(status = 0)
}
