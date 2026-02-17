#!/bin/bash
# Safe STAC catalog build with automatic backup and versioning
#
# This script:
# 1. Backs up current production catalog
# 2. Creates timestamped build directory
# 3. Runs collection and item creation
# 4. Validates output
# 5. Optionally promotes to production
#
# Usage:
#   ./scripts/build_safe.sh [--auto-promote]
#
# Environment variables:
#   STAC_BUILD_DIR - Override build directory (default: timestamped)
#   STAC_PROD_DIR  - Production directory (default: /Users/airvine/Projects/gis/stac_dem_bc/stac/prod)

set -euo pipefail  # Exit on error, undefined vars, pipe failures

# =============================================================================
# Configuration
# =============================================================================

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PROD_BASE="${STAC_PROD_DIR:-/Users/airvine/Projects/gis/stac_dem_bc/stac/prod}"
BUILD_DIR="${STAC_BUILD_DIR:-${PROD_BASE}/build_${TIMESTAMP}}"
BACKUP_DIR="${PROD_BASE}/backup_${TIMESTAMP}"
PROD_DIR="${PROD_BASE}/stac_dem_bc"
LOG_DIR="logs"
AUTO_PROMOTE=false

# Parse arguments
if [[ "${1:-}" == "--auto-promote" ]]; then
    AUTO_PROMOTE=true
fi

# =============================================================================
# Helper Functions
# =============================================================================

log() {
    echo "[$(date +%H:%M:%S)] $*"
}

error() {
    echo "[ERROR] $*" >&2
    exit 1
}

count_jsons() {
    find "$1" -name "*.json" -type f 2>/dev/null | wc -l | tr -d ' '
}

# =============================================================================
# Pre-flight Checks
# =============================================================================

log "=== STAC Safe Build Script ==="
log "Timestamp: $TIMESTAMP"
log "Production dir: $PROD_DIR"
log "Build dir: $BUILD_DIR"
log "Backup dir: $BACKUP_DIR"
log ""

# Check if production exists
if [[ ! -d "$PROD_DIR" ]]; then
    log "⚠️  Production directory doesn't exist (first build?)"
    CURRENT_COUNT=0
else
    CURRENT_COUNT=$(count_jsons "$PROD_DIR")
    log "Current production: $CURRENT_COUNT items"
fi

# Check required files
[[ -f "stac_create_collection.qmd" ]] || error "stac_create_collection.qmd not found"
[[ -f "stac_create_item.qmd" ]] || error "stac_create_item.qmd not found"
[[ -f "data/urls_list.txt" ]] || error "data/urls_list.txt not found"

# =============================================================================
# Step 1: Backup Current Production
# =============================================================================

if [[ $CURRENT_COUNT -gt 0 ]]; then
    log "Step 1: Backing up current production..."
    mkdir -p "$BACKUP_DIR"
    cp -r "$PROD_DIR"/* "$BACKUP_DIR/" 2>/dev/null || true
    BACKUP_COUNT=$(count_jsons "$BACKUP_DIR")
    log "✓ Backed up $BACKUP_COUNT items to: $BACKUP_DIR"
    log ""
else
    log "Step 1: No existing production to back up (first build)"
    log ""
fi

# =============================================================================
# Step 2: Create Build Directory
# =============================================================================

log "Step 2: Creating build directory..."
mkdir -p "$BUILD_DIR"
log "✓ Build directory ready: $BUILD_DIR"
log ""

# =============================================================================
# Step 3: Export Build Path for .qmd Files
# =============================================================================

log "Step 3: Setting build environment..."
export STAC_OUTPUT_DIR="$BUILD_DIR"
log "✓ STAC_OUTPUT_DIR=$STAC_OUTPUT_DIR"
log ""

# =============================================================================
# Step 4: Run Collection Creation
# =============================================================================

log "Step 4: Creating STAC collection..."
mkdir -p "$LOG_DIR"
COLLECTION_LOG="${LOG_DIR}/${TIMESTAMP}_collection.log"

if quarto render stac_create_collection.qmd --execute 2>&1 | tee "$COLLECTION_LOG"; then
    log "✓ Collection created successfully"
else
    error "Collection creation failed - check $COLLECTION_LOG"
fi
log ""

# =============================================================================
# Step 5: Run Item Creation
# =============================================================================

log "Step 5: Creating STAC items (this may take 1-2 hours)..."
ITEMS_LOG="${LOG_DIR}/${TIMESTAMP}_items.log"
START_TIME=$(date +%s)

if quarto render stac_create_item.qmd --execute 2>&1 | tee "$ITEMS_LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    MINUTES=$((DURATION / 60))
    log "✓ Items created successfully in ${MINUTES} minutes"
else
    error "Item creation failed - check $ITEMS_LOG"
fi
log ""

# =============================================================================
# Step 6: Validation
# =============================================================================

log "Step 6: Validating build output..."
BUILD_COUNT=$(count_jsons "$BUILD_DIR")

if [[ $BUILD_COUNT -eq 0 ]]; then
    error "Build validation failed: No JSON files created in $BUILD_DIR"
fi

log "✓ Build validation passed: $BUILD_COUNT items created"

# Check collection.json exists
if [[ ! -f "$BUILD_DIR/collection.json" ]]; then
    error "Build validation failed: collection.json missing"
fi

log "✓ collection.json present"
log ""

# =============================================================================
# Step 7: Promote to Production
# =============================================================================

if [[ "$AUTO_PROMOTE" == true ]]; then
    log "Step 7: Auto-promoting to production..."
    if [[ -d "$PROD_DIR" ]]; then
        rm -rf "$PROD_DIR"
    fi
    mv "$BUILD_DIR" "$PROD_DIR"
    log "✓ Promoted $BUILD_COUNT items to production"
    log ""
else
    log "Step 7: Build complete - manual promotion required"
    log ""
    log "To promote this build to production, run:"
    log "  rm -rf '$PROD_DIR'"
    log "  mv '$BUILD_DIR' '$PROD_DIR'"
    log ""
    log "To rollback to previous version (if needed):"
    if [[ $CURRENT_COUNT -gt 0 ]]; then
        log "  rm -rf '$PROD_DIR'"
        log "  mv '$BACKUP_DIR' '$PROD_DIR'"
    else
        log "  (no backup available - first build)"
    fi
    log ""
fi

# =============================================================================
# Summary
# =============================================================================

log "=== Build Summary ==="
log "Build directory: $BUILD_DIR"
log "Items created: $BUILD_COUNT"
if [[ $CURRENT_COUNT -gt 0 ]]; then
    DELTA=$((BUILD_COUNT - CURRENT_COUNT))
    log "Previous count: $CURRENT_COUNT (${DELTA:+\+}$DELTA)"
    log "Backup location: $BACKUP_DIR"
fi
log "Logs: $COLLECTION_LOG, $ITEMS_LOG"
log ""

if [[ "$AUTO_PROMOTE" == false ]]; then
    log "Next steps:"
    log "1. Verify build output in $BUILD_DIR"
    log "2. Run sync command to upload to S3"
    log "3. Promote to production (see commands above)"
fi

log "✓ Build complete!"
