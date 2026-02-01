# STAC DEM BC Logs

Test execution logs for STAC item creation and validation.

## Naming Convention

```
YYYYMMDD_HHMMSS_test_description.log
```

**Examples:**
- `20260130_073000_test_phase1_10items.log` - Phase 1 test with 10 items
- `20260130_081500_test_validation_cache.log` - Validation caching test
- `20260131_140000_test_incremental_mode.log` - Incremental mode test
- `20260201_100000_prod_full_run.log` - Full production run (not in test mode)

## Log Contents

Captured output from `quarto render stac_create_item.qmd --execute`:
- Configuration (test mode, paths, item counts)
- Validation progress (GeoTIFF checking)
- Item creation progress (parallel processing)
- Errors and warnings
- Performance timing
- Summary statistics

## Retention

Logs are gitignored and kept locally for debugging. Clean up old logs periodically or keep for performance comparison.

## Usage

```bash
# Run test with logging
quarto render stac_create_item.qmd --execute 2>&1 | tee logs/$(date +%Y%m%d_%H%M%S)_test_description.log
```
