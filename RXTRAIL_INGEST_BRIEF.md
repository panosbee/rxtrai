# TASK: RxTrail — Ingest pipeline για τη βάση γνώσης ΕΟΠΥΥ

## ΣΤΟΧΟΣ

Ο uploader του rxtrail.top δέχεται `.xlsx, .xls, .json, .md` αλλά σκάει σε δύο σημεία:

1. `Unexpected token 'I', "Internal S"... is not valid JSON`
   → ο server γυρίζει 500 σε plain text, το frontend κάνει `res.json()` χωρίς έλεγχο.
2. `Cannot read properties of undefined (reading 'join')`
   → ο ingester κάνει `.join()` σε πεδίο που δεν υπάρχει στο schema του αρχείου.

Θέλω ΕΝΑΝ normalizer που δέχεται `.md` και `.json` και γυρίζει πάντα το ίδιο εσωτερικό
σχήμα, χωρίς ποτέ να πετάει undefined.

---

## ΤΑ ΑΡΧΕΙΑ

Τρία αρχεία, ίδιο περιεχόμενο, τρεις μορφές:

| Αρχείο | Περιεχόμενο | Προορισμός |
|---|---|---|
| `rxtrail_eopyy_chunks.json` | 69 chunks, JSON array | RAG (embeddings) |
| `rxtrail_eopyy_kb.json` | 52 SKU + 7 rules + playbook + 8 open questions | MongoDB lookup collection |
| `rxtrail_eopyy_kb.md` | ίδια 69 chunks σε markdown | ανάγνωση / fallback ingest |

Μέγεθος: 180–205 KB το καθένα. UTF-8, ελληνικά.

---

## SCHEMA — ΑΚΡΙΒΩΣ

### `rxtrail_eopyy_chunks.json`

```json
[
  {
    "id": "SKU-41619",
    "title": "41619 — FUSHAN — Καθετήρας γαστροστομίας...",
    "text": "# 41619 — FUSHAN...\n\nΚατηγορία παροχής...",
    "metadata": { "type": "sku", "sku": "41619", "category": "Αναλώσιμο Υγειονομικό Υλικό", "article": "άρθρο 44", "confidence": "ΕΠΙΒΕΒΑΙΩΜΕΝΟ_ΠΡΑΞΗ", "price_eur": 148.65 }
  }
]
```

**ΠΡΟΣΟΧΗ — ΕΔΩ ΕΙΝΑΙ ΤΟ BUG:** τα κλειδιά του `metadata` **ΔΕΝ είναι σταθερά**.
Αλλάζουν ανά `type`:

| `type` | κλειδιά metadata |
|---|---|
| `sku` | `type, sku, category, article, confidence, price_eur` |
| `rule` | `type, confidence, topic` |
| `category` | `type, in_gp_list` |
| `playbook` | `type` |
| `open_questions` | `type` |

Κώδικας που υποθέτει σταθερά κλειδιά ΘΑ ΣΠΑΣΕΙ. Μην κάνεις destructuring με υπόθεση ύπαρξης.

### `rxtrail_eopyy_kb.json`

Object, όχι array. Top-level keys:
`meta, rules, categories, gp_categories_18, skus, agent_playbook, open_questions`

- `skus` → array 52 αντικειμένων. Πεδία: `sku, product, category_system, ekpy_article,
  icd_primary[], icd_primary_note, icd_secondary_examples[], prescriber{}, participation,
  duration_months, qty_listed, qty_observed, qty_max_confirmed, required_fields[],
  blockers[], price_eur, confidence, observed_pattern{}`
- **Δεν έχουν όλα τα SKU όλα τα πεδία.** Προαιρετικά: `observed_pattern, category_warning,
  excluded_sites, family_rule, aya, duration_note`.
- `rules` → dict με 7 keys, κάθε τιμή `{id, title, confidence, body}`.

### `rxtrail_eopyy_kb.md`

Chunks χωρισμένα με `\n---\n`. Κάθε chunk ξεκινά με `# Τίτλος`.
Πρώτο block = header αρχείου (τίτλος, έκδοση, νομικό πλαίσιο) — **πέταξέ το**.

---

## ΤΙ ΝΑ ΥΛΟΠΟΙΗΣΕΙΣ

### 1. `normalize(path) -> list[dict]`

Ένα σημείο εισόδου. Δέχεται `.md` ή `.json`. Γυρίζει πάντα:

```python
{
  "id": str,        # ποτέ κενό
  "title": str,     # ποτέ None
  "text": str,      # ποτέ None
  "tags": list[str],# ΠΑΝΤΑ list, ποτέ undefined
  "metadata": dict, # ΠΑΝΤΑ dict, ποτέ None
}
```

```python
import json, os, re


def normalize(path: str) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    raw = open(path, encoding="utf-8").read()

    if ext == ".md":
        docs = _from_md(raw)
    elif ext == ".json":
        docs = _from_json(json.loads(raw))
    else:
        raise ValueError(f"Μη υποστηριζόμενη μορφή: {ext}")

    return [_shape(d, i) for i, d in enumerate(docs)]


def _from_md(raw: str) -> list[dict]:
    out = []
    for block in raw.split("\n---\n"):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^#\s+(.+)", block)
        title = m.group(1).strip() if m else ""
        # πέτα το header block του αρχείου
        if title.startswith("RxTrail —") and "Έκδοση" in block:
            continue
        out.append({"title": title, "text": block, "metadata": {"type": "md_chunk"}})
    return out


def _from_json(data):
    # chunks.json -> array έτοιμο
    if isinstance(data, list):
        return data

    # kb.json -> flatten σε chunks
    if isinstance(data, dict):
        out = []

        for s in data.get("skus") or []:
            out.append({
                "id": f"SKU-{s.get('sku')}",
                "title": f"{s.get('sku')} — {s.get('product')}",
                "text": _sku_to_text(s),
                "metadata": {
                    "type": "sku",
                    "sku": s.get("sku"),
                    "category": s.get("category_system"),
                    "article": s.get("ekpy_article"),
                    "confidence": s.get("confidence"),
                    "price_eur": s.get("price_eur"),
                },
            })

        for key, r in (data.get("rules") or {}).items():
            if not isinstance(r, dict):
                continue
            out.append({
                "id": r.get("id") or f"RULE-{key}",
                "title": r.get("title") or key,
                "text": r.get("body") or "",
                "metadata": {"type": "rule", "topic": key,
                             "confidence": r.get("confidence")},
            })

        for c in data.get("categories") or []:
            out.append({
                "id": f"CAT-{c.get('name','')[:24]}",
                "title": c.get("name") or "",
                "text": json.dumps(c, ensure_ascii=False, indent=1),
                "metadata": {"type": "category", "in_gp_list": c.get("in_gp_list")},
            })

        return out

    raise ValueError("Άγνωστη δομή JSON")


def _sku_to_text(s: dict) -> str:
    """Flatten SKU σε αναγνώσιμο κείμενο. Ανθεκτικό σε κενά πεδία."""
    def line(label, val):
        if val in (None, "", [], {}):
            return ""
        if isinstance(val, (list, tuple)):
            val = " · ".join(str(v) for v in val)
        elif isinstance(val, dict):
            val = " · ".join(f"{k}: {v}" for k, v in val.items())
        return f"{label}: {val}\n"

    t = f"# {s.get('sku')} — {s.get('product')}\n\n"
    t += line("Κατηγορία παροχής", s.get("category_system"))
    t += line("Άρθρο ΕΚΠΥ", s.get("ekpy_article"))
    t += line("ΚΥΡΙΑ διάγνωση", s.get("icd_primary"))
    t += line("Σημείωση", s.get("icd_primary_note"))
    t += line("ΔΙΑΓΝΩΣΗ 2 (συμμετοχή)", s.get("icd_secondary_examples"))
    t += line("Ποιος γνωματεύει", s.get("prescriber"))
    t += line("Ποσότητα τιμοκαταλόγου", s.get("qty_listed"))
    t += line("Παρατηρημένη ποσότητα", s.get("qty_observed"))
    t += line("Επιβεβαιωμένο μέγιστο", s.get("qty_max_confirmed") or "ΑΓΝΩΣΤΟ")
    t += line("Διάρκεια (μήνες)", s.get("duration_months"))
    t += line("Συμμετοχή", s.get("participation"))
    t += line("Υποχρεωτικά πεδία", s.get("required_fields"))
    t += line("Blockers", s.get("blockers"))
    t += line("Τιμή (€)", s.get("price_eur"))
    t += line("Βεβαιότητα", s.get("confidence"))
    return t


def _shape(d: dict, i: int) -> dict:
    """Εγγύηση τύπων. Ό,τι κι αν λείπει, γυρίζει σωστό τύπο."""
    if not isinstance(d, dict):
        d = {}

    meta = d.get("metadata")
    if not isinstance(meta, dict):
        meta = {}

    text = d.get("text") or d.get("content") or d.get("body") or ""

    # tags από ΟΤΙΔΗΠΟΤΕ υπάρχει στο metadata — χωρίς υπόθεση κλειδιών
    tags = [str(v) for v in meta.values() if v not in (None, "", [], {})]

    return {
        "id": str(d.get("id") or f"chunk-{i}"),
        "title": str(d.get("title") or ""),
        "text": str(text),
        "tags": tags,
        "metadata": meta,
    }
```

### 2. Backend — μην αφήσεις 500 να φύγει ως HTML

```python
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse
import tempfile, os, traceback

router = APIRouter()
ALLOWED = {".json", ".md", ".xlsx", ".xls"}
MAX_BYTES = 10 * 1024 * 1024


@router.post("/api/kb/upload")
async def upload_kb(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": f"Μη αποδεκτή μορφή: {ext}"})

    data = await file.read()
    if len(data) > MAX_BYTES:
        return JSONResponse(status_code=413,
                            content={"ok": False, "error": "Το αρχείο ξεπερνά τα 10MB"})

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(data)
    tmp.close()

    try:
        docs = normalize(tmp.name)
        inserted = await ingest(docs)   # δικό σου
        return {"ok": True, "count": len(docs), "inserted": inserted}
    except Exception as e:
        traceback.print_exc()
        # ΠΑΝΤΑ JSON, ποτέ HTML stack trace
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": str(e),
                                     "type": type(e).__name__})
    finally:
        os.unlink(tmp.name)
```

Αν υπάρχει reverse proxy (Traefik/nginx) μπροστά, ανέβασε το όριο σώματος:
`client_max_body_size 20M;`

### 3. Frontend — μην κάνεις ποτέ `res.json()` χωρίς έλεγχο

```js
async function uploadKB(file) {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch("/api/kb/upload", { method: "POST", body: form });
  const raw = await res.text();

  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new Error(`Ο server γύρισε μη-JSON (${res.status}): ${raw.slice(0, 200)}`);
  }

  if (!res.ok || !data.ok) {
    throw new Error(data.error || `Σφάλμα ${res.status}`);
  }
  return data;
}
```

Και **παντού** όπου γίνεται `.join()`:

```js
// ΛΑΘΟΣ
const label = doc.tags.join(", ");

// ΛΑΘΟΣ ΚΙ ΑΥΤΟ — δεν σώζει αν το πεδίο είναι string ή object
const label = (doc.tags ?? []).join(", ");

// ΣΩΣΤΟ
const label = (Array.isArray(doc.tags) ? doc.tags : []).join(", ");
```

Ψάξε σε όλο το codebase για `.join(` και βάλε `Array.isArray` guard.

### 4. Δύο ξεχωριστά collections

- **RAG collection** ← `chunks.json` ή `kb.md` (embeddings, semantic search)
- **Lookup collection** ← `kb.json` → `skus` array, index στο `sku`

Ο agent πρέπει να απαντά σε «τιμή του 41619» από **lookup**, όχι από embeddings.
Ακριβής τιμή δεν βγαίνει ποτέ σωστά από semantic search.

---

## ΚΡΙΤΗΡΙΑ ΑΠΟΔΟΧΗΣ

1. `normalize("rxtrail_eopyy_kb.md")` → 69 docs, κανένα με κενό `text`.
2. `normalize("rxtrail_eopyy_chunks.json")` → 69 docs, ίδια `id` με το md κατά ~90%.
3. `normalize("rxtrail_eopyy_kb.json")` → 52 SKU + 7 rules + 8 categories = 67 docs.
4. Κάθε doc: `isinstance(doc["tags"], list)` και `isinstance(doc["metadata"], dict)` → True.
5. Upload αρχείου με σκόπιμα σπασμένο JSON → HTTP 400/500 **με σώμα JSON**, όχι HTML.
6. Καμία κλήση `.join()` στο frontend χωρίς `Array.isArray` guard.
7. Ελληνικά χαρακτήρες σωστά (UTF-8) σε όλη τη διαδρομή — έλεγξε ότι το
   `Σκευάσματα Ειδικής Διατροφής` αποθηκεύεται και ανακτάται ακέραιο.

---

## ΤΙ ΝΑ ΜΗΝ ΚΑΝΕΙΣ

- Μην υποθέσεις σταθερά κλειδιά στο `metadata` — αλλάζουν ανά `type`.
- Μην κάνεις `JSON.parse` σε response χωρίς try/catch.
- Μη βάλεις το `kb.json` στο RAG ως ένα μεγάλο document — flatten το πρώτα.
- Μην αλλάξεις το περιεχόμενο των chunks. Το κείμενο είναι ρυθμισμένο για retrieval.
- Μην προσθέσεις υποστήριξη `.jsonl` — δεν είναι έγκυρο JSON, δεν το χρειάζομαι.
