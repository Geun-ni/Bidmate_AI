"""Create a reproducible, template-grouped development/validation/test split."""

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path


SPLITS = ("development", "validation", "test")
RATIOS = (0.70, 0.15, 0.15)
# These notices were inspected while designing rules before the split existed.
ALREADY_INSPECTED = set("""E012608220 E012608199 E012608054 E012607127 E012608149
E012608198 E012607014 E012608224 E012608047 E012608122""".split())


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def category(title):
    if "감리" in title or "건설사업관리" in title:
        kind = "supervision"
    elif "진단" in title:
        kind = "diagnosis"
    elif "정보통신" in title or "ICT" in title:
        kind = "telecom_ict"
    elif "송전" in title:
        kind = "transmission"
    elif "변전" in title:
        kind = "substation"
    elif "배전" in title:
        kind = "distribution"
    else:
        kind = "other"
    return kind + ("_pq" if "PQ" in title.upper() else "")


def price_band(value):
    try:
        price = int(float(value))
    except (TypeError, ValueError):
        return "unknown"
    if price < 80_000_000:
        return "under_80m"
    if price < 300_000_000:
        return "80m_to_300m"
    if price < 1_000_000_000:
        return "300m_to_1b"
    if price < 5_000_000_000:
        return "1b_to_5b"
    return "5b_plus"


def line_signature(text):
    lines = set()
    for line in text.splitlines():
        normalized = re.sub(r"\d+(?:[.,]\d+)*", "#", line.lower())
        normalized = re.sub(r"\s+", "", normalized)
        if len(normalized) >= 25:
            lines.add(normalized)
    return lines


def make_groups(records, threshold=0.88):
    """Join criteria documents with very similar long-line sets."""
    ids = sorted(records)
    signatures = {notice: line_signature(records[notice]["criteria"]) for notice in ids}
    parent = {notice: notice for notice in ids}

    def root(notice):
        while parent[notice] != notice:
            parent[notice] = parent[parent[notice]]
            notice = parent[notice]
        return notice

    for i, left in enumerate(ids):
        first = signatures[left]
        for right in ids[i + 1:]:
            second = signatures[right]
            if not first or not second:
                continue
            intersection = len(first & second)
            # Near-identical templates may have project-specific sections.
            similarity = intersection / max(len(first), len(second))
            if similarity >= threshold:
                parent[root(right)] = root(left)
    groups = defaultdict(list)
    for notice in ids:
        groups[root(notice)].append(notice)
    return sorted((sorted(members) for members in groups.values()), key=lambda x: x[0])


def assign(groups, records, seed):
    rng = random.Random(seed)
    ordered = groups[:]
    rng.shuffle(ordered)
    ordered.sort(key=len, reverse=True)
    targets = dict(zip(SPLITS, [len(records) * ratio for ratio in RATIOS]))
    counts = Counter()
    by_category = defaultdict(Counter)
    assignments = {}
    overall = Counter(records[n]["category"] for n in records)
    pinned = {group[0] for group in groups
              if ALREADY_INSPECTED.intersection(group) or len(group) >= 10}
    if ALREADY_INSPECTED - set(records):
        raise ValueError("Previously inspected notice missing from input")
    for group in groups:
        if group[0] in pinned:
            for notice in group:
                assignments[notice] = "development"
            counts["development"] += len(group)
            by_category["development"].update(records[n]["category"] for n in group)
    for group in ordered:
        if group[0] in pinned:
            continue
        categories = Counter(records[n]["category"] for n in group)
        def score(split):
            projected = counts[split] + len(group)
            # Prefer meeting target size, then preserving the type mix.
            size_cost = abs(projected - targets[split]) / targets[split]
            overflow = max(0, projected - targets[split]) / targets[split]
            type_cost = sum(abs(by_category[split][kind] + amount -
                                overall[kind] * RATIOS[SPLITS.index(split)])
                            for kind, amount in categories.items()) / max(1, targets[split])
            return (overflow * 3 + size_cost + type_cost * 0.25, SPLITS.index(split))
        choice = min(SPLITS, key=score)
        for notice in group:
            assignments[notice] = choice
        counts[choice] += len(group)
        by_category[choice].update(categories)
    # Improve the whole split after greedy placement. A group always moves intact.
    def total_cost(mapping):
        split_counts = Counter(mapping.values())
        kinds = defaultdict(Counter)
        bands = defaultdict(Counter)
        for notice, split in mapping.items():
            kinds[split][records[notice]["category"]] += 1
            bands[split][records[notice]["price_band"]] += 1
        all_bands = Counter(record["price_band"] for record in records.values())
        size_error = sum(abs(split_counts[s] - targets[s]) for s in SPLITS)
        kind_error = sum(abs(kinds[s][k] - overall[k] * RATIOS[i])
                         for i, s in enumerate(SPLITS) for k in overall)
        band_error = sum(abs(bands[s][b] - all_bands[b] * RATIOS[i])
                         for i, s in enumerate(SPLITS) for b in all_bands)
        return size_error * 3 + kind_error + band_error * 0.4

    current = total_cost(assignments)
    for _ in range(100):
        best = current
        proposal = None
        for index, group in enumerate(groups):
            if group[0] in pinned:
                continue
            old = assignments[group[0]]
            for new in SPLITS:
                if new == old:
                    continue
                trial = assignments.copy()
                trial.update((notice, new) for notice in group)
                cost = total_cost(trial)
                if cost < best - 1e-8:
                    best, proposal = cost, trial
            for other in groups[index + 1:]:
                if other[0] in pinned:
                    continue
                other_split = assignments[other[0]]
                if old == other_split:
                    continue
                trial = assignments.copy()
                trial.update((notice, other_split) for notice in group)
                trial.update((notice, old) for notice in other)
                cost = total_cost(trial)
                if cost < best - 1e-8:
                    best, proposal = cost, trial
        if proposal is None:
            break
        assignments, current = proposal, best
    return assignments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--output", type=Path, default=Path("ai/splits/notices_20261121.csv"))
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--force", action="store_true", help="Replace an existing split manifest")
    args = parser.parse_args()
    attachments = csv_rows(args.folder / "bid_attachment.csv")
    contents = {int(row["attachment_id"]): row for row in csv_rows(args.folder / "bid_document_content.csv")}
    notice_rows = {row["notice_no"]: row for row in csv_rows(args.folder / "bid_notice.csv")}
    records = defaultdict(lambda: {"criteria": "", "notice": ""})
    for attachment in attachments:
        notice = attachment["notice_no"]
        content = contents.get(int(attachment["id"]))
        if not content or content["read_status"] != "SUCCESS":
            continue
        text = "".join(content.get(f"text_{part}", "") or "" for part in (1, 2, 3))
        if text.strip() and attachment["doc_kind"] in ("NOTICE", "CRITERIA"):
            records[notice][attachment["doc_kind"].lower()] += text
    if len(records) != len({a["notice_no"] for a in attachments}):
        raise ValueError("Some notices have no readable source")
    for notice, record in records.items():
        if not record["notice"] or not record["criteria"]:
            raise ValueError(f"Both source kinds required: {notice}")
        meta = notice_rows.get(notice)
        if not meta:
            raise ValueError(f"Missing bid_notice metadata: {notice}")
        record["title"] = meta["title"].strip()
        record["category"] = category(meta["title"])
        record["price_band"] = price_band(meta["presumed_price"])
    groups = make_groups(records)
    assignments = assign(groups, records, args.seed)
    group_id = {notice: f"G{index:03d}" for index, group in enumerate(groups, 1) for notice in group}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and not args.force:
        raise ValueError(f"Split manifest already exists: {args.output}; use --force to replace it")
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("notice_no", "split", "template_group", "category", "price_band", "title"))
        for notice in sorted(records):
            record = records[notice]
            writer.writerow((notice, assignments[notice], group_id[notice],
                             record["category"], record["price_band"], record["title"]))
    report = {
        "seed": args.seed,
        "input_sha256": {name: hashlib.sha256((args.folder / name).read_bytes()).hexdigest()
                         for name in ("bid_attachment.csv", "bid_document_content.csv", "bid_notice.csv")},
        "notices": len(records), "template_groups": len(groups),
        "inspected_notices_pinned_to_development": sorted(ALREADY_INSPECTED),
        "large_template_groups_pinned_to_development": 10,
        "largest_groups": sorted((len(group) for group in groups), reverse=True)[:10],
        "split_counts": dict(Counter(assignments.values())),
        "category_counts": {split: dict(Counter(records[n]["category"] for n in records
                                                if assignments[n] == split)) for split in SPLITS},
        "price_band_counts": {split: dict(Counter(records[n]["price_band"] for n in records
                                                  if assignments[n] == split)) for split in SPLITS},
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("notices", "template_groups", "largest_groups", "split_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
