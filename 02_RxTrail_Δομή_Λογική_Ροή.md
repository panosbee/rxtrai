# RxTrail — Δομή, Λογική, Ροή

**Έγγραφο 2 από 2** · Τεχνική προδιαγραφή. Προϋποθέτει ανάγνωση του `01_RxTrail_Αφήγημα_Προδιαγραφή.md`.

Stack (δεσμευτικό): **Python 3.11+ / FastAPI · MongoDB · Vanilla JS + HTML/CSS (χωρίς framework) · JWT auth**
Infra: Hetzner (Xeon W-2295, 256GB RAM, CPU-only, EU). OCR τοπικά (DeepSeek-OCR ~3B, CPU). LLM: DeepSeek v4 API. Embeddings: Jina API.

---

## 1. Δομή φακέλων

```
rxtrail/
├── backend/
│   ├── main.py                  # FastAPI app, CORS, routers
│   ├── config.py                # env vars (pydantic-settings)
│   ├── db.py                    # Motor async client, indexes
│   ├── auth/
│   │   ├── router.py            # /auth/register /auth/login /auth/me
│   │   ├── security.py          # bcrypt, JWT create/verify
│   │   └── deps.py              # get_current_user, get_org
│   ├── models/                  # Pydantic schemas (request/response/db)
│   │   ├── case.py  doctor.py  source.py  structure.py  contact.py
│   │   ├── sku.py  prescription.py  fulfilment.py  visit.py
│   │   ├── patient.py  user.py  common.py
│   ├── routers/
│   │   ├── cases.py  doctors.py  sources.py  structures.py  contacts.py
│   │   ├── skus.py  prescriptions.py  fulfilments.py  visits.py
│   │   ├── queue.py  week.py  dashboard.py  imports.py  llm.py
│   ├── core/
│   │   ├── state_machine.py     # ΟΛΗ η λογική μεταβάσεων. Καμία αλλού.
│   │   ├── clocks.py            # repeat clock, SLA timers (υπολογισμοί)
│   │   ├── scoring.py           # queue scoring
│   │   └── audit.py             # audit log writer
│   ├── llm/
│   │   ├── client.py            # DeepSeek API wrapper, retries, JSON validation
│   │   ├── schemas.py           # αυστηρά JSON schemas κάθε άρθρωσης
│   │   ├── prompts.py           # όλα τα prompts, versioned constants
│   │   ├── scrubber.py          # PII firewall (regex + NER + name list)
│   │   └── embeddings.py        # Jina client + local vector search
│   ├── ocr/
│   │   ├── engine.py            # DeepSeek-OCR local wrapper (queue-based)
│   │   └── pipeline.py          # photo → OCR → scrub → LLM → confirm payload
│   ├── jobs/
│   │   └── scheduler.py         # APScheduler: nightly scoring, clock checks
│   └── tests/
│       ├── test_state_machine.py   # ΥΠΟΧΡΕΩΤΙΚΑ πλήρες
│       ├── test_closed_silence.py  # ΚΑΜΙΑ ειδοποίηση σε CLOSED — μηδενική ανοχή
│       ├── test_scrubber.py
│       └── test_imports.py
├── frontend/
│   ├── index.html  app.js  api.js  auth.js  styles.css
│   ├── views/       # today.js week.js cases.js case_detail.js network.js dashboard.js settings.js
│   ├── components/  # queue_item.js confirm_card.js kanban.js mic_button.js upload_wizard.js
│   └── sw.js        # service worker: cache shell + outbox για offline καταγραφές
├── docker-compose.yml           # api, mongo, ocr-worker
└── .env.example
```

## 2. MongoDB — Collections & Σχήματα

Κοινά πεδία ΠΑΝΤΟΥ: `_id (ObjectId)`, `org_id (ObjectId)`, `created_at`, `updated_at`, `created_by (user_id)`.
**Κάθε query σε κάθε router φιλτράρει ΥΠΟΧΡΕΩΤΙΚΑ με `org_id`** (multi-tenant από μέρα 1 — app-level isolation, helper `scoped(collection, org_id)` ώστε να μην ξεχνιέται ποτέ).

### `users`
```js
{ email, password_hash, name, org_id, role: "owner"|"member", is_active }
```

### `orgs`
```js
{ name, settings: { repeat_notify_day: 25, at_risk_day: 35, new_sla_days: 5,
                    assessed_sla_days: 7, prescribed_sla_days: 7,
                    photo_retention_days: 30 } }
```

### `skus` — SSOT καταλόγου
```js
{ eopyy_code,                // ΚΛΕΙΔΙ. unique ανά org.
  commercial_name, description, category,
  // category enum: "ONS"|"WOUND"|"CATHETER"|"OSTOMY"|"TRACHEO"|"OXYGEN"|
  //                "ENTERAL"|"ORTHO"|"HYALURONIC"|"ONCO_SUPPORT"|"INCONT"|"OTHER"
  icd10_allowed: [],
  specialties_allowed: [],   // ["SURGEON","INTERNIST","PHYSIATRIST",...]
  reimb_price, patient_copay_pct, max_qty_month, duration_days_default,
  criteria: {},              // ελεύθερο object: bmi_max, weight_loss_pct, ...
  my_margin,                 // προαιρετικό, χειροκίνητο
  active: true,
  name_embedding: [/* Jina vector, για fuzzy matching */] }
// index: {org_id, eopyy_code} unique · {org_id, category} · text index στο commercial_name
```

### `structures`
```js
{ name, type,   // "PUBLIC_HOSP"|"PRIVATE_CLINIC"|"REHAB"|"ELDERLY_HOME"|"DIALYSIS"|"PHARMACY"|"HOMECARE_ORG"|"PRACTICE"|"OTHER"
  city, prefecture, address, phone, geo: {type:"Point", coordinates:[lng,lat]},
  notes, priority: 1|2|3 }
// index: {org_id, prefecture} · 2dsphere στο geo
```

### `doctors`
```js
{ name, specialty, structure_ids: [],
  phone, notes,
  relationship: "COLD"|"WARM"|"FRIEND",
  // Μετρικές (ενημερώνονται από jobs/events — ΔΕΝ γράφονται χειροκίνητα):
  rx_count_90d: 0,               // γνωματεύσεις τελευταίου τριμήνου
  avg_pickup_days: null,         // μέσος χρόνος AT_DOCTOR → PRESCRIBED
  open_cases_count: 0,
  last_visit_at: null }
// index: {org_id, specialty}
```

### `contacts` — gatekeepers
```js
{ name, role_title,   // "HEAD_NURSE"|"WOUND_NURSE"|"STOMA_NURSE"|"DIETITIAN"|"PHYSIO"|"SECRETARY"|"OTHER"
  structure_id, phone, notes, is_also_source: bool }
```

### `sources` — οι πηγές συστάσεων
```js
{ name, type,  // "PHYSIO"|"HOMECARE_CREW"|"NURSE"|"PATIENT_ASSOC"|"DOCTOR"|"WORD_OF_MOUTH"|"OTHER"
  linked_contact_id?, linked_doctor_id?,   // αν η πηγή είναι ήδη επαφή/γιατρός
  phone, notes,
  // Μετρικές (event-driven):
  cases_brought: 0, cases_active: 0, cases_lost: 0, cases_closed: 0,
  last_referral_at: null }
// ΑΠΑΓΟΡΕΥΕΤΑΙ οποιοδήποτε οικονομικό πεδίο σε αυτό το collection.
```

### `cases` — ΤΟ ΚΕΝΤΡΟ
```js
{ status: "NEW"|"ASSESSED"|"AT_DOCTOR"|"PRESCRIBED"|"ACTIVE"|"AT_RISK"|"LOST"|"CLOSED",
  source_id,                       // υποχρεωτικό — κάθε περιστατικό έχει πηγή
  alias,                           // "Παππούς Γάζι — κατάκλιση" (ΨΕΥΔΩΝΥΜΟ, όχι όνομα)
  area, geo?,
  need_categories: [],             // SKU categories enum
  needs_freehand,                  // ό,τι είπε ο χρήστης
  flags: [],                       // ["FAMILY_DECIDES", ...]
  est_monthly_value: null,         // συμπληρώνεται στο ASSESSED
  proposed_skus: [{sku_id, qty}],  // πρόταση LLM + διορθώσεις χρήστη
  doctor_id: null,                 // ορίζεται στο AT_DOCTOR
  doctor_appointment_at: null,
  patient_id: null,                // ΜΟΝΟ όταν γίνει ACTIVE (πρώτη εκτέλεση)
  lost_reason: null,               // enum "PRICE"|"SERVICE"|"DOCTOR"|"OTHER" + lost_reason_text
  closed_reason: null,             // "DECEASED"|"RECOVERED"
  status_history: [{from, to, at, by, note?}],   // ΠΛΗΡΕΣ ιστορικό μεταβάσεων
  next_action: null, next_action_due: null }
// index: {org_id, status} · {org_id, source_id} · {org_id, doctor_id}
```

### `patients` — ΜΟΝΟ ενεργοί πελάτες
```js
{ case_id,                    // 1:1 με case
  name, phone, address,       // τα ελάχιστα λειτουργικά
  caregiver_name?, caregiver_phone?,   // συχνά μιλάς με την κόρη
  consent_at,                 // πότε δόθηκε συγκατάθεση παρόχου
  erased: false }             // right-to-erasure: true ⇒ πεδία αντικαθίστανται με "[ERASED]"
// ΔΕΝ αποθηκεύονται: ΑΜΚΑ, διαγνώσεις, ICD-10 δεμένα στο άτομο.
// Οι κλινικές ανάγκες ζουν στο case ως κατηγορίες/SKU, όχι ως διάγνωση προσώπου.
```

### `prescriptions`
```js
{ case_id?,                   // null αν ήρθε από κανάλι 2 (γιατρός έγραψε αυτόνομα)
  doctor_id,
  lines: [{sku_id, eopyy_code, qty, duration_days}],
  issued_at, expires_at,
  photo_path?, photo_delete_at,    // τοπικό path· διαγράφεται από job
  ocr_confidence?, confirmed_by_user: true }
```

### `fulfilments` — ground truth
```js
{ prescription_id, case_id?, doctor_id,
  lines: [{sku_id, qty, revenue}],
  total_revenue,
  fulfilled_at,
  cycle_number,                 // 1η, 2η, 3η επανάληψη του case
  next_repeat_due,              // fulfilled_at + duration_days
  notify_at }                   // next_repeat_due - (duration - repeat_notify_day)... βλ. clocks.py
```

### `visits` — κανάλι 2
```js
{ target_type: "DOCTOR"|"CONTACT"|"STRUCTURE", target_id,
  at, channel: "IN_PERSON"|"PHONE"|"CONGRESS",
  outcome: "POSITIVE"|"NEUTRAL"|"FRIDGE"|"PRESCRIBED",
  rx_estimate?: {count, category},      // αν outcome=PRESCRIBED (self-report)
  voice_note_text?,                     // transcript, ΗΔΗ scrubbed
  extracted_actions: [{text, due, confirmed}] }
```

### `notifications`
```js
{ kind: "REPEAT_DUE"|"SLA_NEW"|"SLA_ASSESSED"|"SLA_PRESCRIBED"|"AT_RISK"|"ACTION_DUE",
  ref_type, ref_id, due_at, seen: false, dismissed: false }
// ΚΡΙΣΙΜΟ: όταν case → CLOSED ή LOST, διαγράφονται ΣΥΓΧΡΟΝΑ όλα τα
// notifications με ref στο case Ή στα fulfilments/prescriptions του.
```

### `audit_log`
```js
{ user_id, action, collection, doc_id, before?, after?, at }
```

### `import_jobs`
```js
{ kind: "SKU"|"STRUCTURES"|"DOCTORS"|"CONTACTS",
  filename, status: "MAPPING"|"PREVIEW"|"DONE"|"FAILED",
  column_mapping: {},           // πρόταση LLM + έγκριση χρήστη
  report: {inserted, updated, skipped, errors: []},
  diff?: {new: [], changed: [], deactivated: []} }
```

## 3. Το State Machine — `core/state_machine.py`

Μοναδικό σημείο αλήθειας. Κανένας router δεν αλλάζει `case.status` απευθείας — όλοι καλούν `transition(case_id, action, payload, user)`.

```python
TRANSITIONS = {
    ("NEW",        "assess"):     "ASSESSED",    # απαιτεί: need_categories, est_monthly_value?
    ("NEW",        "lose"):       "LOST",        # απαιτεί: lost_reason
    ("NEW",        "close"):      "CLOSED",      # απαιτεί: closed_reason
    ("ASSESSED",   "to_doctor"):  "AT_DOCTOR",   # απαιτεί: doctor_id, appointment_at?
    ("ASSESSED",   "lose"):       "LOST",
    ("ASSESSED",   "close"):      "CLOSED",
    ("AT_DOCTOR",  "prescribe"):  "PRESCRIBED",  # απαιτεί: prescription_id (μόλις δημιουργήθηκε)
    ("AT_DOCTOR",  "lose"):       "LOST",
    ("AT_DOCTOR",  "close"):      "CLOSED",
    ("PRESCRIBED", "fulfil"):     "ACTIVE",      # απαιτεί: fulfilment_id· δημιουργεί patient αν δεν υπάρχει
    ("PRESCRIBED", "lose"):       "LOST",
    ("ACTIVE",     "at_risk"):    "AT_RISK",     # ΜΟΝΟ από scheduler (ημέρα 35)
    ("ACTIVE",     "close"):      "CLOSED",
    ("AT_RISK",    "reactivate"): "ACTIVE",      # νέο fulfilment ήρθε
    ("AT_RISK",    "lose"):       "LOST",
    ("AT_RISK",    "close"):      "CLOSED",
}
```

Side-effects ανά μετάβαση (ΜΕΣΑ στο transition, ατομικά):
- κάθε μετάβαση: append στο `status_history`, audit log, ενημέρωση μετρικών source/doctor.
- `→ AT_DOCTOR`: doctor.open_cases_count++.
- `→ PRESCRIBED`: υπολόγισε pickup_days = now − entered_at_doctor· ενημέρωσε doctor.avg_pickup_days (κυλιόμενος μ.ο.)· doctor.rx_count_90d recompute lazy.
- `→ ACTIVE (fulfil)`: δημιούργησε/δέσε patient (με consent_at)· γράψε fulfilment με cycle_number, next_repeat_due, notify_at· δημιούργησε notification REPEAT_DUE.
- `→ AT_RISK`: δημιούργησε notification AT_RISK στην κορυφή.
- `→ LOST`: **ΥΠΟΧΡΕΩΤΙΚΟ lost_reason** (400 χωρίς αυτό)· source.cases_lost++· διάγραψε ΟΛΑ τα notifications του case.
- `→ CLOSED`: **αθόρυβο κλείσιμο** — διάγραψε ΟΛΑ τα notifications του case ΚΑΙ των fulfilments/prescriptions του, στο ίδιο λογικό transaction. Γράψε test `test_closed_silence.py` που το αποδεικνύει: κλείσε case με ενεργό REPEAT_DUE και βεβαιώσου ότι ΚΑΝΕΝΑ notification δεν επιστρέφεται ποτέ ξανά.

## 4. Ρολόγια & Scheduler — `jobs/scheduler.py` (APScheduler, κάθε νύχτα 03:00 + κάθε πρωί 06:30)

1. **Repeat clock:** για κάθε ACTIVE case: αν `today >= notify_at` και δεν υπάρχει ανοιχτό REPEAT_DUE → δημιούργησέ το. Αν `today >= fulfilled_at + at_risk_day (35)` χωρίς νέο fulfilment → transition `at_risk`.
2. **SLA timers:** NEW >5 μέρες → SLA_NEW notification· ASSESSED >7 → SLA_ASSESSED· PRESCRIBED >7 χωρίς fulfilment → SLA_PRESCRIBED («άλλος πάροχος;»).
3. **Queue scoring** (γράφει `daily_queue` cache):

```python
def score(item):
    s = 0
    if item.kind == "AT_RISK":        s += 50
    if item.kind == "REPEAT_DUE":     s += 40 * urgency_decay(item.days_overdue)
    if item.kind == "SLA_NEW":        s += 35   # ζεστό περιστατικό κρυώνει
    if item.kind == "SLA_PRESCRIBED": s += 30
    if item.kind == "ACTION_DUE":     s += 20
    if item.kind == "VISIT_SUGGEST":  s += 10 + tier_bonus(item)  # κανάλι 2: γιατροί σε σιωπή >45μ
    return s
```
4. **Photo retention:** διάγραψε photos με `photo_delete_at < now`.
5. **Metrics refresh:** doctor.rx_count_90d, source aggregates.

## 5. Πρόγραμμα εβδομάδας — `routers/week.py`

Είσοδος: όλα τα pending items (queue) + γεωγραφία (structure.geo / case.area).
Λογική v1 (απλή, ντετερμινιστική — ΟΧΙ LLM, ΟΧΙ TSP optimizer):
1. Ομαδοποίησε items ανά νομό/ζώνη (Ηράκλειο-πόλη, Ηράκλειο-περιφέρεια, Ρέθυμνο, Χανιά, Λασίθι).
2. Ζώνη με ≥3 ώριμα items ⇒ πρόταση «ημέρας-ζώνης».
3. Fixed appointments (doctor_appointment_at) καρφώνονται πρώτα.
4. UI: 5 στήλες (Δευ–Παρ), drag & drop, κουμπί «Κλείδωσε». Το κλειδωμένο πρόγραμμα τροφοδοτεί το «Σήμερα».

## 6. LLM Layer — `llm/`

### 6.1 Client (`client.py`)
- DeepSeek v4 API, `response_format: json`, temperature 0–0.3 για δόμηση.
- Κάθε κλήση: validate με Pydantic schema → αν fail, 1 retry με το validation error στο prompt → αν ξανά fail, επιστροφή `{"fallback": true}` και το UI ανοίγει χειροκίνητη φόρμα. Ποτέ silent failure, ποτέ unvalidated write.
- Timeout 30s, όλα τα calls logged (prompt version, latency, tokens) στο `llm_calls` collection.

### 6.2 PII Scrubber (`scrubber.py`) — ΤΡΕΧΕΙ ΠΡΙΝ ΑΠΟ ΚΑΘΕ ΕΞΩΤΕΡΙΚΟ CALL
```
pipeline(text):
  1. regex ΑΜΚΑ: \b\d{11}\b  → "[ΑΜΚΑ]"
  2. regex ημερομηνιών γέννησης κοντά σε λέξεις-κλειδιά ("γενν", "ετών ... /19", dd/mm/yyyy) → "[ΗΜ]"
  3. regex τηλεφώνων (10ψήφια που δεν είναι ήδη γνωστά τηλέφωνα γιατρών/δομών) → "[ΤΗΛ]"
  4. NER ελληνικών ονομάτων (spaCy el_core_news_lg, τοπικά) → υποψήφια PERSON
  5. Whitelist: ονόματα από doctors/contacts/sources collections ΔΕΝ αφαιρούνται
  6. Blacklist: ονόματα από patients collection αφαιρούνται ΠΑΝΤΑ → "[ΑΣΘΕΝΗΣ]"
  7. Υπόλοιπα PERSON χωρίς match → "[ΟΝΟΜΑ]" (συντηρητικό default)
```
Unit tests με πραγματικά μοτίβα γνωματεύσεων.

### 6.3 Άρθρωση A — Intake (`POST /llm/intake`)
Είσοδος: transcript (Web Speech API στον browser για STT v1· server-side whisper αργότερα αν χρειαστεί). Το transcript περνά scrubber (ο ασθενής δεν είναι ακόμα στη βάση, άρα NER + συντηρητικό default).
Prompt (σταθερά στο `prompts.py`, versioned `INTAKE_V1`):
```
Είσαι βοηθός καταχώρησης περιστατικών homecare στην Ελλάδα. Από το κείμενο εξάγεις ΜΟΝΟ JSON
κατά το σχήμα. Λαϊκές λέξεις: "πάνες"→INCONT, "σόντα"/"καθετήρας"→CATHETER, "οροί"→ENTERAL(;)
σημείωσέ το ως uncertainty, "κατάκλιση"/"πληγή"→WOUND, "συμπληρώματα"/"ροφήματα"→ONS,
"οξυγόνο"→OXYGEN, "τραχειοστομία"→TRACHEO, "σακούλα"(στομία)→OSTOMY, "νάρθηκας"/"ζώνη"→ORTHO.
Αν η πηγή αναφέρεται ονομαστικά, βάλε το όνομα στο source_name_raw — ΜΗΝ μαντέψεις source_id.
```
Schema εξόδου (`IntakeResult`):
```json
{ "source_name_raw": "Μαρία / Βοήθεια στο Σπίτι",
  "area": "Γάζι",
  "alias_suggestion": "Παππούς Γάζι — κατάκλιση",
  "need_categories": ["WOUND","INCONT"],
  "uncertain_categories": ["ONS"],
  "flags": ["FAMILY_DECIDES"],
  "freehand_summary": "..." }
```
Το backend κάνει fuzzy match το `source_name_raw` στα sources (Jina embeddings ή απλό trigram v1) και προτείνει. UI: κάρτα επιβεβαίωσης → save ως case NEW.

### 6.4 Άρθρωση B — Αξιολόγηση (`POST /llm/assess`)
Είσοδος: freehand περιγραφή αναγκών + top-K SKU από vector search (Jina embedding του κειμένου × name_embeddings του καταλόγου, cosine, τοπικά — τα vectors είναι στη Mongo, K≤30).
Έξοδος schema: `{"proposed": [{"eopyy_code","qty_month","why_one_line"}], "specialties_allowed_union": [...], "warnings": [...]}`.
Κανόνας: το LLM επιλέγει ΜΟΝΟ από τα K που του δόθηκαν (κλειστός κατάλογος στο prompt) — μηδενική ελευθερία να εφεύρει κωδικό.

### 6.5 Άρθρωση C — Γνωμάτευση (`POST /prescriptions/from-photo`)
```
photo upload → save local (uuid path) → queue OCR job (DeepSeek-OCR, CPU, 10-30s/σελίδα, async)
→ raw text → scrubber → LLM structuring (schema: lines[{eopyy_code_raw, name_raw, qty, duration}],
doctor_name_raw, issued_date) → backend matching: eopyy_code exact ή name fuzzy→SKU,
doctor_name→doctors → confirm UI (πεδία επεξεργάσιμα) → save prescription
→ transition("prescribe") αν δεμένο σε case.
```
Το frontend κάνει polling στο `GET /prescriptions/ocr-status/{job_id}` (v1 απλό polling, όχι websockets).

### 6.6 Άρθρωση D — Context πριν από επαφή (`GET /llm/brief/{type}/{id}`)
Είσοδος: τελευταίες 10 σημειώσεις/γεγονότα της οντότητας (ήδη PII-clean by design). Έξοδος: `{"brief": "1-2 προτάσεις"}`. Cache 24h.

### 6.7 Άρθρωση E — Αλήθεια εβδομάδας (job Παρασκευή 16:00)
Είσοδος: aggregates μόνο (counts, revenue ανά διάσταση, μεταβολές). Έξοδος: `{"headline": "...", "insight": "..."}` → κάρτα στο Ταμπλό.

### 6.8 Άρθρωση F — Wizard mapping (`POST /imports/analyze`)
Είσοδος: headers + 5 πρώτες γραμμές του Excel (openpyxl). Έξοδος: `{"mapping": {"Στήλη A":"eopyy_code", ...}, "confidence": {...}}` → preview → έγκριση → import με report. Το diff για επαν-εισαγωγές: κλειδί ο `eopyy_code` (SKU) / `name+structure` (γιατροί).

## 7. API Επιφάνεια (σύνοψη)

```
AUTH    POST /auth/register  POST /auth/login  GET /auth/me
QUEUE   GET  /queue/today            # scored, με το "γιατί" ανά item
WEEK    GET  /week/proposal   POST /week/lock
CASES   POST /cases (intake confirm)  GET /cases?status=  GET /cases/{id}
        POST /cases/{id}/transition {action, payload}     # ΜΟΝΗ οδός αλλαγής status
LLM     POST /llm/intake  POST /llm/assess  GET /llm/brief/{type}/{id}
RX      POST /prescriptions/from-photo  GET /prescriptions/ocr-status/{job}
        POST /prescriptions (χειροκίνητη)  POST /fulfilments
VISITS  POST /visits  GET /visits?target=
NETWORK CRUD /doctors /contacts /sources /structures /skus
IMPORTS POST /imports/analyze  POST /imports/commit  GET /imports/{id}
DASH    GET  /dashboard/month  GET /dashboard/weekly-truth  GET /export/xlsx
NOTIF   GET  /notifications  POST /notifications/{id}/dismiss
```

## 8. Auth & Ασφάλεια

- Register: email+password (bcrypt), δημιουργεί org, user role=owner. JWT (access 24h). Middleware βάζει `request.state.org_id` — όλα τα queries scoped.
- HTTPS only (nginx + certbot), rate limit στα /auth (slowapi), CORS locked στο δικό μας origin.
- Uploads: μόνο image/*, xlsx· max 15MB· τυχαία ονόματα· εκτός web root.
- `.env`: secrets. Το repo δεν περιέχει ΚΑΝΕΝΑ κλειδί.

## 9. Frontend αρχές

- Vanilla JS, ES modules, χωρίς build step. Ένα `api.js` (fetch wrapper + JWT + retry queue).
- Mobile-first: ο χρήστης είναι στο αμάξι. Κουμπιά μεγάλα, ροές ≤3 tap.
- **Οι 3 ιερές ροές** (μετρημένες σε δευτερόλεπτα, βλ. έγγραφο 1 §11): καταγραφή επίσκεψης <20s, intake <30s, φωτο γνωμάτευσης = 2 tap + επιβεβαίωση.
- Service worker: cache του shell + **outbox** για visits/intake όταν δεν υπάρχει σήμα (νοσοκομειακά υπόγεια)· sync on reconnect· last-write-wins.
- Mic button: Web Speech API (el-GR), fallback σε πεδίο κειμένου.

## 10. Σειρά υλοποίησης (milestones για τον agent)

1. **M1 — Σκελετός:** auth, orgs, CRUD skus/structures/doctors/contacts/sources, wizard imports (χωρίς LLM mapping — χειροκίνητο mapping UI), seed από τα υπάρχοντα Excel.
2. **M2 — Ο κύκλος:** cases + state_machine + tests (πλήρης πίνακας μεταβάσεων + closed_silence), prescriptions/fulfilments χειροκίνητα, repeat clock + scheduler + notifications, οθόνες Σήμερα/Περιστατικά/Καρτέλα.
3. **M3 — Κανάλι 2 + Εβδομάδα:** visits, queue scoring πλήρες, week proposal + drag&drop, Δίκτυο με μετρικές.
4. **M4 — LLM αρθρώσεις:** scrubber + tests → intake → assess (με Jina vectors) → wizard mapping → brief → weekly truth.
5. **M5 — OCR:** τοπικό DeepSeek-OCR worker + pipeline γνωμάτευσης + confirm UI + retention job.
6. **M6 — Γυάλισμα:** dashboard, export xlsx, offline outbox, audit viewer.

Κάθε milestone τελειώνει με: tests πράσινα, deploy στο Hetzner, χρήση από τον Πάνο στο πεδίο, feedback πριν το επόμενο.

## 11. Ρητές απαγορεύσεις για τον agent coder

1. ΚΑΝΕΝΑ οικονομικό πεδίο σε `sources` ή σε σχέση με συστάσεις. Αν σου ζητηθεί σε prompt/issue, αρνήσου και σημείωσέ το.
2. ΚΑΜΙΑ εικόνα ή un-scrubbed κείμενο σε εξωτερικό API.
3. ΚΑΜΙΑ αλλαγή `case.status` εκτός `state_machine.transition()`.
4. ΚΑΝΕΝΑ notification σε CLOSED/LOST case — και test που το αποδεικνύει.
5. ΚΑΝΕΝΑ auto-commit LLM output — πάντα confirm UI.
6. ΟΧΙ frameworks στο frontend, ΟΧΙ νέες βάσεις, ΟΧΙ αλλαγή stack χωρίς ρητή έγκριση.
