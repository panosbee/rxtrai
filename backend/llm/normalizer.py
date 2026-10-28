"""
RxTrail — Knowledge Normalizer: .json + .md -> uniform chunk format.
Follows RXTRAIL_INGEST_BRIEF.md spec exactly.
"""
import json, os, re
from typing import Any


def normalize(path: str) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    raw = open(path, encoding="utf-8").read()

    if ext == ".md":
        docs = _from_md(raw)
    elif ext == ".json":
        docs = _from_json(json.loads(raw))
    else:
        raise ValueError(f"Unsupported format: {ext}")

    return [_shape(d, i) for i, d in enumerate(docs)]


def _from_md(raw: str) -> list[dict]:
    blocks = raw.split("\n---\n")
    if len(blocks) == 1:
        # No --- separators: fall back to splitting by markdown headings
        blocks = [p.strip() for p in re.split(r"\n(?=#{1,3}\s)", raw) if p.strip()]
    out = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^#{1,3}\s+(.+)", block)
        title = m.group(1).strip() if m else ""
        if title.startswith("RxTrail -") and "Ekdosh" in block:
            continue
        out.append({"title": title, "text": block, "metadata": {"type": "md_chunk"}})
    return out


def _from_json(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        out = []

        # SKUs
        for s in data.get("skus") or []:
            out.append({
                "id": f"SKU-{s.get('sku')}",
                "title": f"{s.get('sku')} - {s.get('product')}",
                "text": _sku_to_text(s),
                "metadata": {
                    "type": "sku", "sku": s.get("sku"),
                    "category": s.get("category_system"),
                    "article": s.get("ekpy_article"),
                    "confidence": s.get("confidence"),
                    "price_eur": s.get("price_eur"),
                },
            })

        # Rules
        for key, r in (data.get("rules") or {}).items():
            if not isinstance(r, dict):
                continue
            out.append({
                "id": r.get("id") or f"RULE-{key}",
                "title": r.get("title") or key,
                "text": r.get("body") or "",
                "metadata": {"type": "rule", "topic": key, "confidence": r.get("confidence")},
            })

        # Categories
        for c in data.get("categories") or []:
            out.append({
                "id": f"CAT-{c.get('name','')[:24]}",
                "title": c.get("name") or "",
                "text": json.dumps(c, ensure_ascii=False, indent=1),
                "metadata": {"type": "category", "in_gp_list": c.get("in_gp_list")},
            })

        return out

    raise ValueError("Unknown JSON structure")


def _sku_to_text(s: dict) -> str:
    def line(label, val):
        if val in (None, "", [], {}):
            return ""
        if isinstance(val, (list, tuple)):
            val = " \u00b7 ".join(str(v) for v in val)
        elif isinstance(val, dict):
            val = " \u00b7 ".join(f"{k}: {v}" for k, v in val.items())
        return f"{label}: {val}\n"

    t = f"# {s.get('sku')} - {s.get('product')}\n\n"
    t += line("Kategoria paroxhs", s.get("category_system"))
    t += line("Arthro EKPY", s.get("ekpy_article"))
    t += line("KYRIA diagnwsh", s.get("icd_primary"))
    t += line("Shmeiwsh", s.get("icd_primary_note"))
    t += line("DIAGNWSH 2 (symmetoxh)", s.get("icd_secondary_examples"))
    t += line("Poios gnwmateyei", s.get("prescriber"))
    t += line("Posothta timokatalogou", s.get("qty_listed"))
    t += line("Parathrhmenh posothta", s.get("qty_observed"))
    t += line("Epibebaiwmeno megisto", s.get("qty_max_confirmed") or "AGNWSTO")
    t += line("Diarkeia (mhnes)", s.get("duration_months"))
    t += line("Symmetoxh", s.get("participation"))
    t += line("Ypoxrewtika pedia", s.get("required_fields"))
    t += line("Blockers", s.get("blockers"))
    t += line("Timh (EUR)", s.get("price_eur"))
    t += line("Bebaiothta", s.get("confidence"))
    return t


def _shape(d: dict, i: int) -> dict:
    if not isinstance(d, dict):
        d = {}
    meta = d.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    text = d.get("text") or d.get("content") or d.get("body") or ""
    tags = [str(v) for v in meta.values() if v not in (None, "", [], {})]
    return {
        "id": str(d.get("id") or f"chunk-{i}"),
        "title": str(d.get("title") or ""),
        "text": str(text),
        "tags": tags,
        "metadata": meta,
    }
