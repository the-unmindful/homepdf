from __future__ import annotations


def parse_page_selection(selection: str | None, page_count: int) -> list[int]:
    """Parse 1-based page selections like '1-3,6,9-'. Returns unique 0-based indices."""
    if page_count <= 0:
        return []

    if selection is None or not selection.strip():
        return list(range(page_count))

    seen: set[int] = set()
    ordered: list[int] = []

    for token in selection.split(","):
        item = token.strip()
        if not item:
            continue

        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = 1 if start_text == "" else int(start_text)
            end = page_count if end_text == "" else int(end_text)
            if start < 1 or end < 1 or start > page_count or end > page_count:
                raise ValueError(f"Page range '{item}' is out of bounds for {page_count} pages.")
            if end < start:
                raise ValueError(f"Page range '{item}' has reversed bounds.")

            for page in range(start, end + 1):
                zero_based = page - 1
                if zero_based not in seen:
                    seen.add(zero_based)
                    ordered.append(zero_based)
            continue

        page = int(item)
        if page < 1 or page > page_count:
            raise ValueError(f"Page '{page}' is out of bounds for {page_count} pages.")
        zero_based = page - 1
        if zero_based not in seen:
            seen.add(zero_based)
            ordered.append(zero_based)

    if not ordered:
        raise ValueError("No valid pages were selected.")

    return ordered


def parse_split_ranges(range_text: str, page_count: int) -> list[tuple[int, int]]:
    """Parse split ranges like '1-3,4-10' into 0-based inclusive tuples."""
    if not range_text or not range_text.strip():
        raise ValueError("Split ranges are required.")

    ranges: list[tuple[int, int]] = []
    for token in range_text.split(","):
        item = token.strip()
        if not item:
            continue
        if "-" not in item:
            page = int(item)
            if page < 1 or page > page_count:
                raise ValueError(f"Page '{page}' is out of bounds for {page_count} pages.")
            ranges.append((page - 1, page - 1))
            continue

        start_text, end_text = item.split("-", 1)
        if not start_text or not end_text:
            raise ValueError(
                "Split ranges must use closed ranges like '1-4'. For open ranges use split-by-N."
            )
        start = int(start_text)
        end = int(end_text)
        if start < 1 or end < 1 or start > page_count or end > page_count:
            raise ValueError(f"Split range '{item}' is out of bounds for {page_count} pages.")
        if end < start:
            raise ValueError(f"Split range '{item}' has reversed bounds.")
        ranges.append((start - 1, end - 1))

    if not ranges:
        raise ValueError("No valid split ranges provided.")

    return ranges
