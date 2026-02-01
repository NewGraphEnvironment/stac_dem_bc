#!/usr/bin/env Rscript
# Benchmark: Time to fetch BC DEM URL list from objectstore

# Auto-install tictoc if not present
if (!require("tictoc", quietly = TRUE)) {
  pak::pkg_install("tictoc")
  library(tictoc)
}

library(ngr)

timestamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
log_file <- sprintf("logs/%s_benchmark_url_fetch.log", timestamp)

# Redirect output to log
sink(log_file, split = TRUE)

cat("Benchmarking URL fetch from BC objectstore...\n")
cat("Expected: ~22,548 DEM files\n\n")

# Time the fetch operation
tic("Total fetch time")

keys <- ngr::ngr_s3_keys_get(
  url_bucket = "https://nrs.objectstore.gov.bc.ca/gdwuts",
  prefix = "",
  pattern = c("dem", "*.tif")
)

elapsed <- toc()

# Results
cat("\n")
cat("=== RESULTS ===\n")
cat(sprintf("Files fetched: %d\n", length(keys)))
cat(sprintf("Time elapsed: %.2f seconds (%.2f minutes)\n",
            elapsed$toc - elapsed$tic,
            (elapsed$toc - elapsed$tic) / 60))
cat(sprintf("Rate: %.1f files/second\n",
            length(keys) / (elapsed$toc - elapsed$tic)))
cat(sprintf("\nLog saved to: %s\n", log_file))

sink()

cat(sprintf("Benchmark complete! Results in %s\n", log_file))
