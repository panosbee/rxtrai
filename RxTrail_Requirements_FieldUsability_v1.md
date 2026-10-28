# RxTrail — Απαιτήσεις Βελτίωσης Καθημερινής Χρήσης (Πεδίο)

**Spec για coding agent · Έκδοση 1.0 · Ιούλιος 2026 · Infosphere Technologies**
Βάση: RxTrail Εγχειρίδιο v1.0. Το παρόν προσθέτει/τροποποιεί — δεν αντικαθιστά.

---

## 0. Καθολικές συμβάσεις (ΥΠΟΧΡΕΩΤΙΚΕΣ — ισχύουν σε ΟΛΑ τα REQ)

1. **State machine μόνο στο `backend/core/state_machine.py`.** Καμία μετάβαση κατάστασης δεν γράφεται σε router, model ή frontend. Νέες μεταβάσεις → μόνο εκεί.
2. **Κάθε μετάβαση** καταγράφεται σε `status_history` (embedded) **και** στο audit log (`core/audit.py`).
3. **Κοινά πεδία παντού:** `_id`, `org_id`, `created_at`, `updated_at`, `created_by`. Κάθε νέο query φιλτράρει με `org_id` (org isolation — υπάρχει test, μην το σπάσεις).
4. **LLM/AI writes: πάντα confirmation πριν το write.** Καμία νέα λειτουργία δεν κάνει silent write μέσω agent.
5. **Backward compatible migrations.** Νέα πεδία → optional με default. Υπάρχοντα documents χωρίς το πεδίο πρέπει να συνεχίζουν να δουλεύουν (διάβασε με `.get(field, default)`).
6. **Enum τιμές = SCREAMING_SNAKE_CASE**, ελληνικά labels μόνο στο frontend.
7. Κάθε REQ έχει tests που πρέπει να περνούν **πριν** θεωρηθεί done.

**Σειρά υλοποίησης (dependencies):** REQ-02 → REQ-01 → REQ-05 → REQ-06 → REQ-03 → REQ-04.
Αιτιολογία: το `no_repeat` (02) καθαρίζει τον θόρυβο και το χρειάζονται clocks/scoring· οι μεταβάσεις (01) είναι prerequisite για σωστό funnel· το one-tap (05) & contact log (06) είναι αυτόνομα· το scoring (03) & geo (04) καταναλώνουν τα παραπάνω.

---

## REQ-01 — Έξοδοι από PRESCRIBED (καταγραφή χαμένης δουλειάς)

### Πρόβλημα
Όταν γράφεται γνωμάτευση αλλά ο ασθενής πάει σε άλλον πάροχο ή δεν εκτελείται ποτέ, το case κολλάει στο `PRESCRIBED` χωρίς νόμιμη έξοδο. Είναι το **#1 σημείο διαρροής εσόδων** και σήμερα δεν καταγράφεται.

### Στόχος
Νόμιμες μεταβάσεις εξόδου από `PRESCRIBED` και `AT_DOCTOR`, με δομημένο λόγο απώλειας και competitor, ώστε να μετριέται το leak.

### Data model — `models/case.py`
Πρόσθεσε:
| Πεδίο | Τύπος | Default | Περιγραφή |
|---|---|---|---|
| `lost_reason` | enum \| null | null | `WENT_TO_COMPETITOR`, `NOT_EXECUTED`, `NO_ANSWER`, `PATIENT_DECLINED`, `MEDICAL_CHANGE`, `OTHER` |
| `lost_competitor` | string \| null | null | Ελεύθερο κείμενο — ποιος πάροχος το πήρε |
| `lost_note` | string \| null | null | Σχόλιο |
| `fulfil_sla_due` | datetime \| null | null | Deadline εκτέλεσης μετά το PRESCRIBED (βλ. clocks) |

### State machine — `core/state_machine.py`
Πρόσθεσε μεταβάσεις:
| Από | Προς | Action | Side effects |
|---|---|---|---|
| `PRESCRIBED` | `LOST` | `lose` | set `lost_reason`+`lost_competitor`(optional)· doctor `open_cases_count -1`· clear notifications |
| `PRESCRIBED` | `AT_RISK` | (auto, scheduler) | όταν `now > fulfil_sla_due` και δεν έγινε fulfil· notification «γράφτηκε, δεν εκτελέστηκε» |
| `AT_DOCTOR` | `ASSESSED` | `reassign` | ξε-ανάθεση γιατρού (`doctor_id=null`)· doctor `open_cases_count -1`· λόγος στο `status_history` |

**Επικαιροποίηση κανόνων:** ο παλιός κανόνας «καμία μετάβαση προς LOST/CLOSED από PRESCRIBED» **αίρεται μερικώς** → επιτρέπεται `PRESCRIBED→LOST` (μέσω `lose`). Παραμένει απαγορευμένο `PRESCRIBED→CLOSED` (το CLOSED σημαίνει ανάρρωση/απεβίωση, όχι εμπορική απώλεια).

**Validation:** `lose` από `PRESCRIBED`/`AT_DOCTOR`/`ACTIVE`/`AT_RISK`/`NEW`/`ASSESSED` απαιτεί `lost_reason`. Αν `lost_reason==WENT_TO_COMPETITOR` → `lost_competitor` required.

### Backend — `routers/cases.py`
`POST /cases/{id}/transition` δέχεται body: `{ "action": "lose"|"reassign", "lost_reason"?, "lost_competitor"?, "lost_note"?, "new_doctor_id"? }`. Το endpoint **μόνο** καλεί `state_machine.transition(...)`· καμία λογική μετάβασης εδώ.

### Frontend — `views/cases.js`, `components/bottomSheet.js`
- Στην κάρτα/modal ενός `PRESCRIBED` case: κουμπιά **«Πήγε αλλού»** (→ `lose`, reason default `WENT_TO_COMPETITOR`) και **«Δεν εκτελέστηκε»** (→ `lose`, reason `NOT_EXECUTED`).
- Bottom sheet ζητά competitor (chips με γνωστούς + free text) και προαιρετικό σχόλιο.
- Σε `AT_DOCTOR` case: κουμπί **«Αλλαγή γιατρού»** → `reassign` + picker νέου γιατρού (προαιρετικά κάνει και `to_doctor` αμέσως).

### Acceptance criteria / tests — `tests/test_state_machine.py`
- `PRESCRIBED→LOST` επιτρέπεται με reason, απορρίπτεται χωρίς reason.
- `PRESCRIBED→CLOSED` παραμένει **απαγορευμένο** (raise).
- `WENT_TO_COMPETITOR` χωρίς `lost_competitor` → validation error.
- `reassign` μηδενίζει `doctor_id` και μειώνει `open_cases_count` του παλιού γιατρού.
- Καμία ειδοποίηση δεν παράγεται για case που πήγε `LOST` (επέκταση `test_closed_silence.py` → `test_lost_silence`).

---

## REQ-02 — Προϊόντα/περιστατικά χωρίς επανάληψη (`no_repeat`)

### Πρόβλημα
Ενέσιμο υαλουρονικό & ορθωτικά είναι **εφάπαξ**. Με σταθερό ρολόι 30 ημερών χτυπούν ψεύτικο `AT_RISK` και πνίγουν την οθόνη «Σήμερα».

### Στόχος
Τα εφάπαξ να μην αποκτούν ρολόι επανάληψης και να μην γίνονται ποτέ `AT_RISK` λόγω repeat.

### Data model
`models/sku.py` — πρόσθεσε:
| Πεδίο | Τύπος | Default | Περιγραφή |
|---|---|---|---|
| `no_repeat` | bool | `false` | Εφάπαξ προϊόν (χωρίς κύκλο) |

`models/case.py` — πρόσθεσε:
| Πεδίο | Τύπος | Default | Περιγραφή |
|---|---|---|---|
| `no_repeat` | bool | `false` | Παράγεται στο fulfil (βλ. κανόνα) — καθορίζει αν ανοίγει ρολόι |

**Κανόνας παραγωγής:** στο `fulfil` (PRESCRIBED→ACTIVE), αν **όλα** τα SKU items της εκτέλεσης έχουν `no_repeat==true` → `case.no_repeat=true`. Αν έστω ένα επαναλαμβανόμενο → `false`. (ΥΠΟΘΕΣΗ: το Fulfilment κρατά `items:[{sku_id, qty}]`· αν δεν υπάρχει, πρόσθεσέ το.)

### State machine / clocks — `core/state_machine.py`, `core/clocks.py`
- Στο `fulfil`: αν `case.no_repeat==true` → **μην** θέτεις `repeat_clock_start`/`next_repeat_due` (μένουν null). Το case πάει `ACTIVE` αλλά χωρίς ρολόι.
- `core/clocks.py`: ο έλεγχος επανάληψης αγνοεί cases με `no_repeat==true` **ή** `next_repeat_due==null`.

### Scheduler — `jobs/scheduler.py`
Το nightly `ACTIVE→AT_RISK` auto-transition **skip** για `no_repeat==true`. (Ένα `no_repeat` ACTIVE case ουσιαστικά είναι «ολοκληρωμένο recurring-wise»· UI βλ. κάτω.)

### Frontend
- `views/network.js` (SKU tab): checkbox **«Εφάπαξ (χωρίς επανάληψη)»**.
- `components/repeatRing.js`: αν `no_repeat` → **μην** ζωγραφίζεις δακτύλιο· δείξε badge **«Εφάπαξ»** αντί για ρολόι.
- Import mapping (REQ-Excel): προαιρετική στήλη «Εφάπαξ»/`no_repeat` → boolean (Ναι/true → true).

### Acceptance criteria / tests — `tests/test_clocks.py` (νέο)
- Fulfil με SKU `no_repeat` → `next_repeat_due==null` και `repeat_clock_start==null`.
- Nightly scheduler δεν κάνει ποτέ `no_repeat` case → `AT_RISK`.
- Mixed cart (1 repeat + 1 no_repeat) → `case.no_repeat==false`, ρολόι ανοίγει κανονικά.

---

## REQ-03 — Value-weighted queue scoring

### Πρόβλημα
Το scoring είναι σταθερό (50/38/25/20/15). Ασθενής οξυγόνου €900 σε κίνδυνο κάθεται κάτω από ONS €40.

### Στόχος
Η ουρά της ημέρας να προτεραιοποιεί κατά **προτεραιότητα × αξία**, ώστε τα ακριβά recurring να είναι πάνω.

### Backend — `core/scoring.py`
Διατήρησε τα υπάρχοντα base weights ως `base_score` (bucket):
`AT_RISK=50, REPEAT_DUE=38, NEW_SLA=25, ASSESSED_SLA=20, RX_SLA=15`.

Νέος τελικός τύπος:
```
value_factor = 1 + min(est_monthly_value, VALUE_CAP) / VALUE_CAP   # ∈ [1, 2]
final_score  = base_score * value_factor
```
- `VALUE_CAP = 500` (config, tunable στο `config.py`).
- Αν `est_monthly_value` λείπει/0 → `value_factor=1` (καμία τιμωρία).
- **Tie-break:** ίσο `final_score` → μικρότερο `next_repeat_due` (πιο επείγον χρονικά) πρώτα.
- `no_repeat` cases (REQ-02): δεν μπαίνουν στο REPEAT_DUE bucket.

**Γιατί πολλαπλασιαστικό, όχι αθροιστικό:** ένα €900 AT_RISK (50×2=100) πρέπει να ξεπερνά ένα €40 AT_RISK (50×1.08≈54) αλλά ΟΧΙ να ισοπεδώνει ένα φθηνό AT_RISK κάτω από ακριβό NEW (25×2=50 < 54). Ο πολλαπλασιασμός κρατά την προτεραιότητα κατάστασης ως πρωτεύουσα και την αξία ως δευτερεύουσα.

### API — `GET /queue/today`
Κάθε στοιχείο επιστρέφει επιπλέον: `base_score`, `value_factor`, `final_score`, `est_monthly_value`. Ταξινόμηση κατά `final_score desc`. **Μην** αλλάξεις το shape των υπαρχόντων πεδίων — μόνο πρόσθεση.

### Frontend — `views/today.js`
Η κάρτα ουράς δείχνει το ίδιο «γιατί», αλλά πρόσθεσε διακριτικό badge αξίας (π.χ. `€900/μ`). Το score που εμφανίζεται = `final_score` (στρογγυλό).

### Acceptance criteria / tests — `tests/test_scoring.py` (νέο)
- €900 AT_RISK > €40 AT_RISK > €900 NEW (με τα caps).
- Missing value → factor 1 (κανένα crash).
- Tie-break κατά `next_repeat_due`.

---

## REQ-04 — Geography-aware ουρά «Σήμερα»

### Πρόβλημα
Η «Σήμερα» λέει *τι*, όχι *τι είναι κοντά μεταξύ του*. Στο πεδίο θες «αυτά τα 3 στο Ηράκλειο μαζί».

### Στόχος
Ομαδοποίηση της ημερήσιας ουράς κατά περιοχή, χωρίς να χαλάει η προτεραιότητα.

### Backend — `routers/queue.py`
`GET /queue/today?group_by=area` (default `none` για backward compat). Όταν `area`:
- Ομαδοποίησε τα items κατά `case.area` (normalize: trim/lowercase για matching, εμφάνισε original).
- **Σειρά ομάδων:** κατά `max(final_score)` της ομάδας (η ομάδα με το πιο επείγον/ακριβό πρώτη).
- Εντός ομάδας: κατά `final_score desc` (REQ-03).
- Items χωρίς `area` → ομάδα «Χωρίς περιοχή» στο τέλος.
Response: `{ "groups": [ { "area": "Ηράκλειο", "group_score": 100, "items": [...] }, ... ] }`.

**ΥΠΟΘΕΣΗ (μελλοντικό, μη μπλοκάρον):** αν αργότερα υπάρχουν lat/lng, αντικατάστησε το string-grouping με clustering. Τώρα το `area` string αρκεί.

### Frontend — `views/today.js`
Toggle **«Κατά περιοχή»** (persist προτίμηση τοπικά). Όταν ON: collapsible headers ανά περιοχή με counter + άθροισμα €. Το default παραμένει flat λίστα.

### Acceptance criteria / tests
- Grouping διατηρεί σχετική σειρά προτεραιότητας εντός ομάδας.
- Ομάδα με το υψηλότερο `final_score` εμφανίζεται πρώτη.
- Κενό `area` → «Χωρίς περιοχή» τελευταία.
- `group_by=none` → πανομοιότυπο με σημερινό output (regression).

---

## REQ-05 — One-tap επανάληψη (repeat fulfilment)

### Πρόβλημα
Το ~80% της δουλειάς είναι «ίδια γνωμάτευση, +30 μέρες». Αν θέλει φόρμα, θα αποφεύγεται.

### Στόχος
Από ACTIVE case: **ένα tap** → προσυμπληρωμένο από την τελευταία εκτέλεση → επιβεβαίωση → reset ρολογιού, `cycle_number+1`.

### Data model
- Το case ήδη έχει `cycle_number`. Πρόσθεσε `last_fulfilment_id` (ObjectId|null) στο `models/case.py`, ενημερώνεται σε κάθε fulfil. (Εναλλακτικά query του πιο πρόσφατου Fulfilment — αλλά το πεδίο αποφεύγει round-trip.)

### Backend — `routers/fulfilments.py` (ή `routers/cases.py`)
Νέο: `POST /cases/{id}/repeat`
- Preconditions: `status==ACTIVE`, `no_repeat==false`.
- Δημιουργεί νέο Fulfilment **αντιγράφοντας items/qty/duration** από `last_fulfilment_id`, με `created_at=now`.
- Καλεί `state_machine.transition(action="fulfil")` (ACTIVE→ACTIVE): reset `repeat_clock_start=now`, `next_repeat_due=now+duration` (duration από SKU/prescription, βλ. REQ-02 default), `cycle_number+1`, ενημέρωση `last_fulfilment_id`.
- **Idempotency guard:** αν έγινε ήδη repeat < 24h (config `REPEAT_MIN_GAP_HOURS`), επίστρεψε 409 με μήνυμα (αποφυγή διπλού tap). Override flag `force=true`.
- Επιστρέφει το ενημερωμένο case + νέο `next_repeat_due`.

**Idempotency:** το endpoint δέχεται προαιρετικό `Idempotency-Key` header· ίδιο key εντός 60s → επιστρέφει το ίδιο αποτέλεσμα (όχι διπλή εγγραφή).

### Frontend — `views/cases.js`, `views/today.js`
- Κουμπί **«🔁 Επανάληψη»** πάνω σε κάθε ACTIVE card **και** στο queue item τύπου REPEAT_DUE/AT_RISK.
- Tap → confirm sheet με σύνοψη («Ίδια εκτέλεση: 2× Επίθεμα X, ρολόι → 30 μέρες») → **Επιβεβαίωση**. Χωρίς πλήρη φόρμα.
- Μετά την επιβεβαίωση: optimistic UI (ρολόι πράσινο) + toast· σε 409 δείξε «Έγινε ήδη πρόσφατα».

### Acceptance criteria / tests — `tests/test_repeat.py` (νέο)
- Repeat σε ACTIVE → `cycle_number+1`, `next_repeat_due` reset, νέο Fulfilment με ίδια items.
- Repeat σε `no_repeat` case → 400 (δεν επιτρέπεται).
- Repeat σε μη-ACTIVE → 409/400.
- Διπλό tap εντός `REPEAT_MIN_GAP_HOURS` → μία μόνο εγγραφή.

---

## REQ-06 — Log προσπαθειών επικοινωνίας (χαμένες κλήσεις)

### Πρόβλημα
Πήρες τον ασθενή, δεν σήκωσε — δεν υπάρχει πού να το γράψεις. Ή ξεχνάς ποιον πήρες ή τον ενοχλείς 3 φορές.

### Στόχος
Ελαφρύ, in-case log επαφής: αποτέλεσμα + πότε να ξαναπροσπαθήσεις, χωρίς να αλλάζει κατάσταση.

### Data model — `models/case.py`
Embedded array `contact_attempts`:
| Πεδίο | Τύπος | Περιγραφή |
|---|---|---|
| `at` | datetime | Πότε έγινε |
| `channel` | enum | `CALL`, `SMS`, `IN_PERSON`, `OTHER` |
| `outcome` | enum | `NO_ANSWER`, `REACHED`, `CALLBACK_REQUESTED`, `WRONG_NUMBER` |
| `next_try_at` | datetime \| null | Πότε να ξαναπροσπαθήσεις |
| `note` | string \| null | Σχόλιο |
| `by` | user_id | Ποιος (created_by) |

**Δεν είναι transition.** Δεν αγγίζει `status`. Γράφεται στο audit log ως `contact_attempt`.

### Backend — `routers/cases.py`
- `POST /cases/{id}/contact` → append attempt.
- Το attempt **δεν** αλλάζει state. Εξαίρεση συμπεριφοράς: αν `outcome==NO_ANSWER` σε `AT_RISK` case, **δεν** το βγάζει από AT_RISK (μόνο `reactivate`/`fulfil` το κάνει).

### Scoring integration (ελαφρύ, με REQ-03)
- Αν υπάρχει `next_try_at <= today`, το item ανεβαίνει ελαφρώς: `final_score += CONTACT_DUE_BONUS` (config, default 5). Δείχνει «να ξαναπάρω σήμερα».
- Αν τελευταίο `outcome==NO_ANSWER` και `next_try_at` στο μέλλον → **μην** το προτείνεις σήμερα (θόρυβος).

### Frontend — `views/cases.js` (case detail), `views/today.js`
- Στο case detail: μικρή timeline «Επικοινωνία» + κουμπί **«📞 Καταγραφή κλήσης»** → sheet (outcome chips + «ξανά σε: αύριο/2 μέρες/βδομάδα» → set `next_try_at`).
- Στο queue item: quick action **«Δεν απάντησε»** (one-tap `NO_ANSWER` + default `next_try_at=+2 μέρες`).
- Badge στο case: «Τελευταία επαφή: πριν 2μ — δεν απάντησε».

### Acceptance criteria / tests — `tests/test_contact.py` (νέο)
- `POST /contact` δεν μεταβάλλει `status` (κανένα state change σε καμία κατάσταση).
- `next_try_at<=today` → +CONTACT_DUE_BONUS στο score.
- `NO_ANSWER` με `next_try_at` στο μέλλον → δεν εμφανίζεται στη σημερινή ουρά.
- Attempt γράφεται στο audit log.

---

## 7. Cross-cutting

### 7.1 Migrations (`jobs/` ή one-off script)
Όλα τα νέα πεδία optional με default → **δεν** απαιτείται backfill για να μη σκάσει το app. Προαιρετικό backfill script:
- `sku.no_repeat=false` όπου λείπει.
- `case.no_repeat=false`, `case.contact_attempts=[]`, `case.lost_reason=null` όπου λείπουν.
Idempotent (τρέξε ξανά χωρίς παρενέργειες).

### 7.2 Config (`config.py`) — νέα keys
```
VALUE_CAP=500
REPEAT_MIN_GAP_HOURS=24
CONTACT_DUE_BONUS=5
FULFIL_SLA_DAYS=7          # REQ-01: deadline εκτέλεσης μετά το PRESCRIBED
```

### 7.3 Audit (`core/audit.py`)
Νέοι τύποι event: `transition:lose`, `transition:reassign`, `repeat`, `contact_attempt`. Κάθε ένα με `case_id`, `org_id`, `by`, `payload`.

### 7.4 Definition of Done (όλα τα REQ)
- [ ] Νέα/ενημερωμένα tests πράσινα, **μαζί** με τα υπάρχοντα `test_state_machine.py`, `test_closed_silence.py`, `test_imports.py`, org-isolation.
- [ ] Κανένα state change εκτός `state_machine.py`.
- [ ] `GET /queue/today?group_by=none` = regression-identical με πριν.
- [ ] Documents χωρίς τα νέα πεδία δουλεύουν (backward compat).
- [ ] Καμία ειδοποίηση σε `LOST`/`CLOSED`/`no_repeat` (silence tests).

### 7.5 Εκτός scope (μην τα αγγίξεις τώρα)
- Πραγματικοί κωδικοί ΕΟΠΥΥ / τιμές αποζημίωσης (χωριστό data layer — δεν το φέρνει αυτό το spec).
- lat/lng geo-clustering (REQ-04 μένει σε `area` string).
- Οικονομικά πεδία αμοιβών/ποσοστών (εκτός προϊόντος by design).

---

**Τέλος spec · v1.0 · Infosphere Technologies**
