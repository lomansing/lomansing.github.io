#!/usr/bin/env python3
"""
Convert a Blogger Atom export feed into Jekyll _posts markdown files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ATOM_NS = "http://www.w3.org/2005/Atom"
BLOGGER_NS = "http://schemas.google.com/blogger/2018"
NS = {"atom": ATOM_NS, "blogger": BLOGGER_NS}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Blogger Atom feed to Jekyll posts."
    )
    parser.add_argument(
        "--input",
        default="feed.atom",
        help="Path to Blogger Atom feed (default: feed.atom).",
    )
    parser.add_argument(
        "--output-dir",
        default="_posts",
        help="Directory for generated Jekyll posts (default: _posts).",
    )
    parser.add_argument(
        "--include-non-live",
        action="store_true",
        help="Include entries whose blogger:status is not LIVE.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing post files if present.",
    )
    return parser.parse_args()


def get_text(parent: ET.Element, xpath: str, default: str = "") -> str:
    node = parent.find(xpath, NS)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value


def parse_datetime(iso_text: str) -> dt.datetime:
    # Handles Blogger timestamps like 2018-04-25T09:09:00.003Z
    parsed = dt.datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def format_jekyll_datetime(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S %z")


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def clean_html_content(raw_html: str) -> str:
    text = raw_html or ""
    text = html.unescape(text)

    # Treat explicit line break tags as paragraph separators.
    text = re.sub(r"(?i)<br\s*/?>", "\n\n", text)

    # Remove all remaining HTML tags.
    text = re.sub(r"<[^>]+>", "", text)

    # Convert non-breaking spaces into normal spaces.
    text = text.replace("\u00a0", " ")

    # Normalize trailing spaces and repeated blank lines.
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_labels(entry: ET.Element) -> list[str]:
    labels: list[str] = []
    for category in entry.findall("atom:category", NS):
        term = (category.attrib.get("term") or "").strip()
        if not term:
            continue
        if term not in labels:
            labels.append(term)
    return labels


def pick_slug(entry: ET.Element, title: str, fallback_index: int) -> str:
    blogger_filename = get_text(entry, "blogger:filename")
    if blogger_filename:
        stem = Path(blogger_filename).stem
        stem_slug = slugify(stem)
        if stem_slug:
            return stem_slug

    title_slug = slugify(title)
    if title_slug:
        return title_slug

    entry_id = get_text(entry, "atom:id")
    id_match = re.search(r"post-(\d+)", entry_id)
    if id_match:
        return f"post-{id_match.group(1)}"

    return f"post-{fallback_index}"


def build_front_matter(
    title: str, published: dt.datetime, labels: list[str], permalink: str
) -> str:
    lines = [
        "---",
        "layout: post",
        f"title: {yaml_quote(title)}",
        f"date: {format_jekyll_datetime(published)}",
    ]

    if labels:
        quoted_labels = ", ".join(yaml_quote(label) for label in labels)
        lines.append(f"categories: [{quoted_labels}]")

    if permalink:
        lines.append(f"permalink: {yaml_quote(permalink)}")

    lines.append("---")
    return "\n".join(lines)


def convert_feed(
    input_path: Path,
    output_dir: Path,
    include_non_live: bool,
    overwrite: bool,
) -> tuple[int, int]:
    tree = ET.parse(input_path)
    root = tree.getroot()

    output_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    used_paths: set[Path] = set()
    entry_index = 0

    for entry in root.findall("atom:entry", NS):
        entry_index += 1
        post_type = get_text(entry, "blogger:type")
        if post_type != "POST":
            continue

        status = get_text(entry, "blogger:status")
        if status != "LIVE" and not include_non_live:
            continue

        title = get_text(entry, "atom:title", default="Untitled")
        content = get_text(entry, "atom:content")
        published_raw = get_text(entry, "atom:published")
        if not published_raw:
            # Skip malformed entry without published timestamp.
            skipped += 1
            continue
        published = parse_datetime(published_raw)

        permalink = get_text(entry, "blogger:filename")
        labels = extract_labels(entry)

        slug = pick_slug(entry, title, entry_index)
        filename = f"{published.strftime('%Y-%m-%d')}-{slug}.md"
        output_path = output_dir / filename

        # Keep filenames unique when duplicates occur on same date.
        dup_counter = 2
        while output_path in used_paths:
            output_path = output_dir / (
                f"{published.strftime('%Y-%m-%d')}-{slug}-{dup_counter}.md"
            )
            dup_counter += 1
        used_paths.add(output_path)

        if output_path.exists() and not overwrite:
            skipped += 1
            continue

        front_matter = build_front_matter(
            title=normalize_space(title) or "Untitled",
            published=published,
            labels=labels,
            permalink=permalink,
        )

        body = clean_html_content(content)
        output = f"{front_matter}\n\n{body}\n"
        output_path.write_text(output, encoding="utf-8", newline="\n")
        created += 1

    return created, skipped


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    created, skipped = convert_feed(
        input_path=input_path,
        output_dir=output_dir,
        include_non_live=args.include_non_live,
        overwrite=args.overwrite,
    )

    print(f"Done. Created {created} posts, skipped {skipped} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
