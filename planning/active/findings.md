# Findings: STAC DEM BC Performance Optimization

Research, technical discoveries, and trade-off analysis for automated STAC updates.

---

## Dataset Context

**Source:** BC Provincial DEM Archive (nrs.objectstore.gov.bc.ca/gdwuts)
- **Total files:** 22,548 GeoTIFFs
- **Coverage:** British Columbia (114°W to 140°W, 48°N to 60°N)
- **Format:** Mix of COG and non-COG TIFFs
- **Invalid files:** ~100-500 (corrupt, unreadable)
- **Update frequency:** Weekly additions (typically 10-50 new files)

**Baseline performance (sequential):**
- Full processing: 5-6 hours
- Validation: Not cached, validated on-demand
- Spatial extent calculation: ~20 minutes per rebuild
- No incremental update capability

---

## Technical Discoveries

### 1. Parallel Processing for Rasterio

**Finding:** ThreadPoolExecutor works, multiprocessing doesn't

**Context:** Processing 22K+ remote GeoTIFFs via `/vsicurl/` requires parallel execution for performance.

**Testing:**
- `multiprocessing.Pool`: Causes rasterio threading conflicts, hangs/crashes
- `ThreadPoolExecutor`: Works reliably with rasterio, clean error handling
- **Recommendation:** Use ThreadPoolExecutor for all rasterio-based geospatial processing

**Reference implementation:** `/Users/airvine/Projects/repo/stac_orthophoto_bc/stac_create_item.qmd:186-193`

**Expected improvement:** 4-8x speedup for item creation (~4h → ~30-45min)

---

### 2. Validation Caching Strategy

**Finding:** Pre-validation with caching dramatically improves iteration speed

**Approach:**
1. Run `rio cogeo validate` on all URLs in parallel
2. Cache results in `data/stac_geotiff_checks.csv` (url, is_geotiff, is_cog)
3. Use lookup dictionary during item creation
4. Skip unreadable files entirely (no retry, log warning)

**Trade-offs:**
- **Pro:** Frontload validation cost once, subsequent runs fast
- **Pro:** Incremental runs only validate new files
- **Pro:** Skip 100-500 invalid files (saves 10-30 minutes)
- **Con:** Initial validation takes time (~20-30 min for 22K files)

**Implementation:** `stac_create_item.qmd:105-139`

---

### 3. COG Detection for Media Type Assignment

**Finding:** Media type should reflect actual COG status, not assumption

**Implementation:**
```python
media_type = (
    "image/tiff; application=geotiff; profile=cloud-optimized"
    if check["is_cog"] else
    "image/tiff; application=geotiff"
)
```

**Validation command:** `rio cogeo validate /vsicurl/<url>`

**Output parsing:**
- `"is a valid cloud optimized GeoTIFF"` → COG
- `"is NOT a valid cloud optimized GeoTIFF"` → Non-COG but readable
- Other output → Unreadable (skip)

**Related:** Resolves stac_dem_bc#3 (GeoTIFF validation and media type)

---

### 4. Spatial Extent Trade-off: Calculated vs Hardcoded

**Issue:** [stac_dem_bc#4](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/4)

**Calculated approach:**
```python
def bbox_combined(paths, max_workers=8):
    # Extract bbox from each GeoTIFF in parallel
    # Compute union via shapely
    # Returns precise data extent
```
- **Time:** ~20 minutes for 22K files
- **Accuracy:** Exact data extent
- **Use case:** Projects where data extent != known boundary

**Hardcoded approach:**
```python
bbox = [-140, 48, -114, 60]  # BC provincial boundary
spatial_extent = pystac.SpatialExtent([bbox])
```
- **Time:** Instant
- **Accuracy:** Slightly less precise (includes areas with no coverage)
- **Trade-off rationale:** BC boundary is stable, well-defined, saves 20min

**Decision:** Use hardcoded for stac_dem_bc (provincial dataset). Preserve `bbox_combined()` function for other projects (e.g., UAV imagery with unknown extent).

**Location:** `stac_create_collection.qmd:39-69` (function preserved, commented)

---

### 5. Incremental Update Architecture

**Challenge:** Processing all 22,548 files weekly wastes time when only 10-50 are new

**Solution:** Change detection + incremental processing

**Architecture:**
1. **Change detection script** (`detect_changes.py`):
   - Fetch current BC DEM directory listing
   - Compare with cached `data/urls_list.txt`
   - Output: `data/urls_new.txt`, `data/urls_deleted.txt`
   - Exit code: 0 if no changes (skip update), 1 if changes

2. **Incremental mode flag** in `stac_create_item.qmd`:
   - `incremental=True`: Read `urls_new.txt`, append to collection
   - `incremental=False`: Read `urls_list.txt`, rebuild collection

3. **Validation cache integration**:
   - Only validate new URLs not in cache
   - Reuse cached results for existing files

**Expected improvement:** Weekly 5-6h → 5-15min (100x+ for typical updates)

---

### 6. Test Mode for Rapid Iteration

**Finding:** Test mode essential for development without waiting hours

**Implementation:**
```python
test_only = True
test_number_items = 10

urls_to_check = path_items[:test_number_items] if test_only else path_items
```

**Benefit:** Validate code changes in minutes instead of hours

---

### 7. Progress Visibility with tqdm

**Finding:** Long-running parallel jobs need progress feedback

**Implementation:**
```python
with concurrent.futures.ThreadPoolExecutor() as executor:
    results = list(tqdm(executor.map(process_item, urls_to_check),
                       total=len(urls_to_check),
                       desc="Creating STAC Items"))
```

**Benefit:** Visual confirmation of progress, estimate completion time

---

## Infrastructure Context

### S3 Bucket Management (awshak)

**Current state:** S3 buckets IaC-managed via OpenTofu/Terraform in `/Users/airvine/Projects/repo/awshak`

**Existing buckets:**
- `stac-dem-bc` (prod) - Already exists
- `stac-orthophoto-bc` (prod)
- `imagery-uav-bc` (prod)
- `backup-imagery-uav`
- `water-temp-bc`

**Features:** Versioning, lifecycle policies, CORS, public access controls

**Testing:** Easy to create `dev-stac-dem-bc` via awshak modules

**Future:** Migrate VM provisioning to awshak for full IaC reproducibility (currently manual via `vm_upload_run()`)

---

### VM Deployment Pattern

**Current approach (Phase 3):**
- Manual deployment via `vm_upload_run()` function from stac_uav_bc
- Reference: `/Users/airvine/Projects/repo/stac_uav_bc/scripts/functions.R:15-45`
- Target VM: stac-prod (DigitalOcean droplet 480789025)

**Future migration:**
- Use awshak Terraform modules for reproducible VM provisioning
- Version-controlled server configuration
- Trackable infrastructure changes

---

## Reference Implementations

**stac_orthophoto_bc patterns:**
- Parallel validation: Lines 106-122
- Parallel item creation: Lines 142-204
- Test mode: Lines 74-76
- Validation caching: Lines 88, 123-139

**stac_uav_bc patterns:**
- VM deployment: `vm_upload_run()` function
- Registration scripts: `scripts/config/stac_register.sh`

---

## Dependencies

**Python packages:**
- `pystac` - STAC specification
- `rio_stac` - Rasterio-based STAC item creation
- `rasterio` - Geospatial raster I/O
- `rio-cogeo` - COG validation (`rio cogeo validate`)
- `pandas` - Validation cache management
- `tqdm` - Progress bars
- `concurrent.futures` - Parallel execution (built-in)

**System requirements:**
- `rio` CLI tools (from rasterio[cogeo])
- AWS CLI (S3 sync)
- PgSTAC database access

---

## Performance Benchmarks

**Baseline (sequential):**
- Full processing: 5-6 hours
- Item creation: ~4 hours (22,548 files)
- Validation: On-demand, no cache
- Spatial extent: ~20 minutes

**Target (parallel + caching):**
- Full processing: 1-1.5 hours
- Item creation: ~30-45 minutes (4-8x speedup)
- Validation: ~20-30 minutes (one-time), then cached
- Spatial extent: Instant (hardcoded)

**Weekly incremental:**
- Typical: 10-50 new files
- Expected: 5-15 minutes (change detection + validation + creation + sync)

**Measurement:**
- Track via `benchmarks.csv` on VM (date, file counts, phase timings)
- SRED evidence: Quantifiable performance improvements

---

## Test vs Production Path Isolation

**Finding:** Test mode must use separate output directory from production

**Problem discovered:** Original code hardcoded output to `prod/stac_dem_bc/` even in test mode, risking accidental production contamination.

**Solution:**
```python
if test_only:
    path_local = "/Users/airvine/Projects/gis/stac_dem_bc/stac/dev/stac_dem_bc"  # Safe testing
else:
    path_local = "/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc"  # Production
```

**Directory structure:**
```
/Users/airvine/Projects/gis/stac_dem_bc/stac/
├── dev/stac_dem_bc/       # Test output (never uploaded to S3)
│   └── collection.json
└── prod/stac_dem_bc/      # Production output (gets uploaded to S3)
    ├── collection.json
    └── [22,548+ item JSONs]
```

**Benefit:** True isolation - test runs cannot accidentally overwrite production data

---

## Open Questions

1. **Deletion handling:** How to safely remove items from PgSTAC? Manual review first?
2. **Cache rebuild frequency:** Monthly full validation to catch drift?
3. **Error notification:** Email on failure? Health check endpoint?
4. **Rollback procedure:** How to revert to previous collection state if registration fails?

---

**Last updated:** 2026-01-30 (Test safety enhancement)
