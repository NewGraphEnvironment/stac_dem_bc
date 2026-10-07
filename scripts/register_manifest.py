"""
Item ids from source GeoTIFF URLs -- the one registration helper that knows
where this catalogue's items come from.

Everything else that used to live here (id sets, body digests, fetching,
NDJSON, the homogeneity audit) is source-agnostic and is now the `stacs`
package (#49), configured by `stacs.toml`. This is what feeds it a subset:

    .venv/bin/python scripts/register_manifest.py ids-from-urls \\
        --urls-file data/urls_new.txt > ids.txt
    .venv/bin/stacs register --config stacs.toml --mode ids --ids-file ids.txt

stacs refuses the run, before writing, if any id has no published item -- and
urls_new.txt includes sources item_create skipped. In such a month use
`stacs register --mode drift`, which needs no list.
"""

import argparse
import sys

from stac_utils import PATH_S3, url_scheme_check, url_to_item_id


def item_ids_from_urls(urls) -> list[str]:
    """Map source GeoTIFF URLs to STAC item ids.

    Raises on any URL outside the objectstore prefix. `url_to_item_id` slices
    by prefix *length* without checking the prefix matches, so an unexpected
    host silently yields a mangled id rather than an error — the id would look
    plausible and register against nothing.

    The source lists store `https://`. A one-slash `https:/` line (ngr < 0.0.3)
    raises in `url_scheme_check` instead of reading as a foreign host (#51).
    """
    ids = []
    for url in urls:
        url = url_scheme_check(url.strip())
        if not url:
            continue
        if not url.startswith(PATH_S3):
            raise ValueError(f"URL is not on the objectstore ({PATH_S3}): {url}")
        if not url.endswith(".tif"):
            raise ValueError(f"URL is not a GeoTIFF: {url}")
        ids.append(url_to_item_id(url))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ids-from-urls", help="item ids from source GeoTIFF URLs")
    p.add_argument("--urls-file", required=True)
    args = ap.parse_args()

    if args.cmd == "ids-from-urls":
        with open(args.urls_file) as f:
            for item_id in item_ids_from_urls(f):
                print(item_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
