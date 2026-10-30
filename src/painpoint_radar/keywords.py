"""Review and maintain catalog keywords. CLI: painpoint-radar keywords list|import|seed."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
from collections.abc import Iterable


def normalize(term: str) -> str:
    """Normalize exported terms for storage and duplicate comparison."""
    return " ".join(term.casefold().strip().strip("\"'").split())


def provider_aliases(catalog: dict) -> list[str]:
    return list(dict.fromkeys(alias for aliases in catalog.get("providers", {}).values() for alias in aliases))


def list_keywords(catalog: dict) -> None:
    keywords = catalog["product"]["keywords"]
    groups = [(name, keywords.get(name, [])) for name in ("core", "generic")]
    generic = {term.casefold() for term in keywords.get("generic", [])}
    groups.append(("competitors", [alias + ("*" if alias.casefold() in generic else "")
                                   for alias in provider_aliases(catalog)]))
    groups += [(f"extra_keywords:{sub}", entry["extra_keywords"])
               for sub, entry in catalog.get("subreddits", {}).items() if entry.get("extra_keywords")]
    groups += [(f"serp_keywords:{name}", terms) for name, terms in catalog.get("serp_keywords", {}).items()
               if name != "_note"]
    for name, terms in groups:
        print(f"{name} ({len(terms)}): {', '.join(terms)}")


def destination(value: str) -> str:
    if value in ("core", "generic") or (value.startswith("serp:") and value[5:].strip()
                                         and value[5:] != "_note"):
        return value
    raise argparse.ArgumentTypeError("--into must be core, generic, or serp:<group> (excluding reserved _note)")


def read_terms(path: Path, col: str | None, volume_col: str | None,
               min_volume: float | None) -> list[tuple[str, str | None]]:
    """Read BOM-marked exports or simple CSV lists, preserving volume text for review."""
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else (
        "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
    text = data.decode(encoding)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|")
        rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error:
        delimiter = "\t" if "\t" in text.partition("\n")[0] else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [row for row in rows if row]
    header = rows[0] if rows else []
    names = [cell.strip().casefold() for cell in header]

    def column(name: str) -> int:
        try:
            return names.index(name.strip().casefold())
        except ValueError:
            raise ValueError(f"column {name!r} not found") from None

    keyword_index = column(col) if col is not None else next(
        (i for i, name in enumerate(names) if name.startswith("keyword")), 0)
    volume_index = column(volume_col) if volume_col is not None else (
        names.index("volume") if "volume" in names else None)
    if min_volume is not None and volume_index is None:
        raise ValueError("--min-volume requires a volume column")
    headerless = (len(header) == 1 and col is None and not names[0].startswith("keyword")
                  and names[0] not in ("term", "query", "volume")
                  and not any(c.isspace() or c.isupper() for c in header[0])
                  and not any(any(numeric(cell) is not None for cell in row) for row in rows[1:]))
    result = []
    for row in rows if headerless else rows[1:]:
        term = row[keyword_index] if keyword_index < len(row) else ""
        volume = row[volume_index].strip() if volume_index is not None and volume_index < len(row) else None
        result.append((term, volume))
    return result


def numeric(value: str | None) -> float | None:
    try:
        return float(value.replace(",", "")) if value else None
    except ValueError:
        return None


def add_terms(catalog: dict, path: Path, into: str, terms: Iterable[tuple[str, str | None]],
              dry_run: bool, min_volume: float | None = None) -> None:
    """Print and apply reviewed additions; imports and site seeds share this path."""
    keywords = catalog["product"]["keywords"]
    if into.startswith("serp:"):
        owner = catalog.setdefault("serp_keywords", {})
        key = into[5:]
        existing = [term for name, group in owner.items() if name != "_note" for term in group]
    else:
        owner, key = keywords, into
        existing = keywords["core"] + keywords.get("generic", []) + provider_aliases(catalog)
        existing += [term for entry in catalog.get("subreddits", {}).values()
                     for term in entry.get("extra_keywords", [])]
    seen = {normalize(term) for term in existing}
    added, present, dropped = [], 0, 0
    for raw, volume in terms:
        term = normalize(raw)
        number = numeric(volume)
        if len(term) < 3 or (min_volume is not None and (number is None or number < min_volume)):
            dropped += 1
        elif term in seen:
            present += 1
        else:
            seen.add(term)
            added.append(term)
            print(f"+ {term}" + (f" ({volume})" if volume else ""))
    print(f"{len(added)} added, {present} already present, {dropped} dropped (short or below --min-volume)")
    if not dry_run:
        owner.setdefault(key, []).extend(added)
        path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    from . import pulse
    from . import seed

    try:
        path = Path(args.catalog)
        # Validate with the common loader, but write the original keys without injected defaults.
        pulse.load_catalog(path)
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if "product" not in catalog:
            raise ValueError("keywords requires a catalog with product.keywords")
        if args.keyword_command == "list":
            list_keywords(catalog)
        else:
            if args.keyword_command == "import":
                terms = read_terms(Path(args.file), args.col, args.volume_col, args.min_volume)
            else:
                site = args.site or catalog["product"].get("site")
                if not site:
                    raise ValueError("seed requires --site URL or product.site in the catalog")
                terms = [(term, None) for term in seed.site_keywords(
                    site, max_terms=args.max_terms, min_count=args.min_count)]
            add_terms(catalog, path, args.into, terms, args.dry_run, getattr(args, "min_volume", None))
    except (OSError, ValueError, csv.Error) as error:
        args.keyword_parser.error(str(error))
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the keywords CLI under pulse's top-level subparsers."""
    parser = subparsers.add_parser("keywords", help="list, import, or seed catalog keywords")
    commands = parser.add_subparsers(dest="keyword_command", required=True)
    for command in ("list", "import", "seed"):
        child = commands.add_parser(command)
        child.add_argument("--product", help="product name in the repository catalogs folder")
        child.add_argument("--catalog", help="local catalog path; overrides --product")
        child.set_defaults(fn=run, keyword_parser=child)
        if command == "list":
            continue
        child.add_argument("--dry-run", action="store_true")
        if command == "import":
            child.add_argument("file")
            child.add_argument("--into", required=True, type=destination)
            child.add_argument("--col")
            child.add_argument("--volume-col")
            child.add_argument("--min-volume", type=float)
        else:
            child.add_argument("--into", choices=("core", "generic"), default="core")
            child.add_argument("--site")
            child.add_argument("--max-terms", type=int, default=25)
            child.add_argument("--min-count", type=int, default=3)
