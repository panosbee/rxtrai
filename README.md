# RxTrail — CRM για Παρόχους Υγείας στο Σπίτι

**Περιστατικά · Πλάνο · Δίκτυο · Γνώση · AI Βοηθός (Ασκληπιός)**

Το **RxTrail** είναι ένα πλήρες CRM για ιατρικούς επισκέπτες / παρόχους
αποζημιούμενων προϊόντων υγείας στο σπίτι (ΕΟΠΥΥ). Καλύπτει όλο τον κύκλο:
από τη σύσταση και την αξιολόγηση, στη γνωμάτευση, την εκτέλεση και την
επανάληψη — με AI που διαβάζει τα δεδομένα του κάθε χρήστη και βοηθά
χωρίς γράψιμο.

> Mobile-first PWA · Multi-tenant · Τοπικά embeddings · Web Push · OCR γνωματεύσεων

---

## Πίνακας περιεχομένων

1. [Δυνατότητες](#δυνατότητες)
2. [Αρχιτεκτονική](#αρχιτεκτονική)
3. [Δομή repository](#δομή-repository)
4. [Γρήγορη εκκίνηση (local)](#γρήγορη-εκκίνηση-local)
5. [Μεταβλητές περιβάλλοντος](#μεταβλητές-περιβάλλοντος)
6. [Tests](#tests)
7. [Deployment (production)](#deployment-production)
8. [Ασφάλεια & απομόνωση δεδομένων](#ασφάλεια--απομόνωση-δεδομένων)
9. [Ο Ασκληπιός — AI βοηθός](#ο-ασκληπιός--ai-βοηθός)
10. [API (σύνοψη)](#api-σύνοψη)
11. [Συνεισφορά / Σημειώσεις](#συνεισφορά--σημειώσεις)

---

## Δυνατότητες

### Περιστατικά (cases)
- State machine: `NEW → ASSESSED → AT_DOCTOR → PRESCRIBED → ACTIVE → AT_RISK`,
  με `LOST` / `CLOSED` και πλήρες ιστορικό μεταβάσεων.
- **ΑΜΚΑ** ασθενούς, ψευδώνυμο, περιοχή, κατηγορίες αναγκών, εκτίμηση αξίας.
- **Πολλαπλές γνωματεύσεις ανά περιστατικό** (2η, 3η…) χωρίς αλλαγή κατάστασης.
- **Σημειώσεις περιστατικού** με timestamp → τροφοδοτούν την επεισοδιακή μνήμη του AI.
- Πλήρες **edit** οποιαδήποτε στιγμή + επισύναψη φωτογραφιών/PDF.
- **Αναζήτηση** με ΑΜΚΑ ή όνομα (accent/case-insensitive).
- **Παράδοση περιστατικού** σε συνάδελφο (μεταφέρονται γιατρός, πηγή, έγγραφα, γνώση).

### Πλάνο & Εβδομάδα
- Μηνιαίο ημερολόγιο + εβδομαδιαίος planner (Δευτέρα–Παρασκευή, πλοήγηση εβδομάδων).
- Ραντεβού με γιατρό, τοποθεσία, διάρκεια, conflict detection.
- **Σημείωση ανά ραντεβού** (τι έμαθες) → χρονολόγιο + επεισοδιακή μνήμη για την ΤΝ.
- Αυτόματο πλάνο εβδομάδας από τον Ασκληπιό: διαβάζει **σημειώσεις γιατρών**
  («κάθε Πέμπτη»), σέβεται εργάσιμες, μοιράζει ομοιόμορφα, χωρίς Σαββατοκύριακα.
- Ημερολόγιο ⇄ Εβδομάδα πλήρως συγχρονισμένα.

### Δίκτυο
- **Υγειονομικό προσωπικό** (γιατροί/νοσηλευτές), **δομές**, **πηγές**, **επαφές**, **SKU**.
- Σημειώσεις ανά επαφή, μετρικές (Rx 90ημ., ανοιχτά περιστατικά, τελευταία επίσκεψη).
- **Ψαχτήρι** σε όλες τις λίστες & στα dropdowns (searchable selects).
- Εξαγωγή υγειονομικού προσωπικού σε Excel.

### Γνώση (RAG)
- Ανέβασμα αρχείων (Excel / JSON / Markdown) → chunks + embeddings.
- Η γνώση είναι **ιδιωτική ανά οργανισμό** (multi-tenant isolation).
- Κοινή γνώση για όλους μόνο μέσω **Κοινού Χώρου → 📚**.
- **Τοπικό μοντέλο embeddings** (`intfloat/multilingual-e5-small`, CPU) — χωρίς εξωτερικό API.

### Κοινός Χώρος
- Κοινόχρηστοι φάκελοι/αρχεία με εκδόσεις για όλους τους χρήστες.
- **Ομαδικό chat** με ιστορικό (accordion, polling).

### Γνωματεύσεις & OCR
- Φωτογραφία γνωμάτευσης → **Tesseract OCR (ελληνικά)** → PII scrub → δομημένο JSON
  (κωδικοί, ποσότητες, διάρκεια, γιατρός) → επιβεβαίωση χρήστη.
- Retention φωτογραφιών (προεπιλογή 30 ημέρες).

### Admin
- Κλειστή εγγραφή: **μόνο ο admin δημιουργεί χρήστες** (email + προσωρινός κωδικός,
  υποχρεωτική αλλαγή στην πρώτη είσοδο).
- Λίστα χρηστών με τα δικά τους counts, ενεργοποίηση/απενεργοποίηση, reset κωδικού, διαγραφή.
- **📊 Charts απόδοσης ανά χρήστη** (περιστατικά/γνωματεύσεις/εκτελέσεις/τζίρος ανά μήνα,
  κατανομή καταστάσεων).
- **📥 Excel ανά χρήστη** (Πηγές, Δομές, Περιστατικά, Υγειονομικό) και **📅 εβδομαδιαίο
  πρόγραμμα σε Excel**.

### AI βοηθός «Ασκληπιός»
- Natural language → ενέργειες (δημιουργία/αναζήτηση/μεταβάσεις/πλάνο).
- Βλέπει **όλα** τα δεδομένα του χρήστη σε κάθε ερώτηση (γιατροί + σημειώσεις, δομές,
  πηγές, περιστατικά, ραντεβού) και **συνδυάζει** με RAG γνώση + κατάλογο SKU.
- **Voice intake** → αυτόματη συμπλήρωση περιστατικού (ρωτά για ό,τι λείπει, δεν επινοεί).
- **Ημερήσιο πλάνο** με επιβεβαίωση («οργάνωσέ μου την ημέρα» → «ναι» → ραντεβού).

### Λοιπά
- **Web Push** ειδοποιήσεις (πρωινή σύνοψη: κίνδυνοι, λήξεις, εκκρεμότητες).
- **Offline queue**: ενέργειες χωρίς σήμα → αυτόματο sync όταν επιστρέψει το δίκτυο.
- Rate limit στο login, PWA εγκατάσταση, σκοτεινό-φιλικό mobile UI.

---

## Αρχιτεκτονική

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  Frontend (PWA)             │        │  Backend (FastAPI)           │
│  Vanilla JS ES modules      │  HTTP  │  /api/* routers              │
│  Service Worker · Push      │◄──────►│  Auth (JWT) · Rate limits    │
│  Offline queue (localStorage)│       │  OCR worker (Tesseract)      │
└─────────────────────────────┘        │  Scheduler (APScheduler)     │
                                       │  LLM client (DeepSeek)       │
                                       │  Local embeddings (e5-small) │
                                       └───────────┬──────────────────┘
                                                   │
                                       ┌───────────▼──────────────────┐
                                       │  MongoDB (motor)             │
                                       │  per-org collections         │
                                       └──────────────────────────────┘
```

- **Multi-tenant**: κάθε χρήστης ανήκει σε org — όλα τα δεδομένα φιλτράρονται με `org_id`.
- **AI**: DeepSeek για σύνθεση/απαντήσεις (pro), flash για ταξινόμηση/δρομολόγηση.
  Embeddings & σημασιολογική αναζήτηση τρέχουν **τοπικά** στον server.
- **PII**: scrubber πριν από κάθε εξωτερική κλήση LLM (ΑΜΚΑ, τηλέφωνα, ημερομηνίες).

---

## Δομή repository

```
backend/
  auth/            # JWT, login, αλλαγή κωδικού, colleagues
  core/            # state machine, clocks, scoring
  jobs/            # APScheduler: repeat clock, SLA, push, retention
  llm/             # agent (Ασκληπιός), prompts, schemas, embeddings, memory, knowledge
  models/          # Pydantic schemas
  ocr/             # Tesseract engine + prescription pipeline
  routers/         # REST endpoints (cases, doctors, admin, queue, week, share, …)
  scripts/         # one-off migrations (π.χ. re-embed)
  tests/           # pytest (state machine, isolation smoke tests)
  main.py          # FastAPI app + lifespan (workers/scheduler)
frontend/
  index.html       # app shell (PWA)
  app.js           # navigation, shell, modals, toasts, offline sync
  api.js           # fetch wrapper (JWT, offline queue, stale cache)
  styles.css       # shell styling
  components/      # agent chat, forms, documents, searchSelect, voice, …
  views/           # today, cases, plan, network, dashboard, knowledge, admin, …
  sw.js            # service worker (network-first, push)
deploy.sh          # rsync deploy script (server)
backup.sh          # nightly mongodump + uploads backup
nginx-rxtrail.conf # nginx reverse proxy config
rxtrail.service    # systemd unit
.env.example       # πρότυπο ρυθμίσεων (χωρίς κλειδιά)
```

---

## Γρήγορη εκκίνηση (local)

**Απαιτήσεις:** Python 3.11+ · MongoDB · Tesseract (με ελληνικά) — προαιρετικά για OCR.

```bash
# 1) Ρυθμίσεις
cp .env.example .env
#   συμπλήρωσε JWT_SECRET, DEEPSEEK_API_KEY (τα υπόλοιπα έχουν defaults)

# 2) Dependencies
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements.txt
# CPU-only torch για τα τοπικά embeddings:
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3) MongoDB (local)
#    mongod --dbpath <path>   ή systemd service

# 4) Εκκίνηση
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Άνοιξε: `http://127.0.0.1:8000/app`

> Ο πρώτος χρήστης δημιουργείται μέσω admin panel. Για local dev, όρισε
> `ADMIN_EMAIL` στο `.env` και δημιούργησε χρήστη με `POST /auth/register`
> μόνο αν ενεργοποιήσεις `ALLOW_OPEN_REGISTRATION=true` (προεπιλογή: κλειστό).

---

## Μεταβλητές περιβάλλοντος

Πλήρες πρότυπο: [`.env.example`](.env.example). **Ποτέ μην κάνεις commit το `.env`.**

| Μεταβλητή | Περιγραφή |
|---|---|
| `MONGODB_URL`, `MONGODB_DB` | Σύνδεση MongoDB |
| `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_HOURS` | Υπογραφή/λήξη tokens |
| `ADMIN_EMAIL` | Ο λογαριασμός που βλέπει το `/admin` |
| `ALLOW_OPEN_REGISTRATION` | `false` = μόνο admin δημιουργεί χρήστες |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_MODEL_FAST` | LLM |
| `LOCAL_EMBEDDINGS_MODEL` | Τοπικό μοντέλο embeddings (default `intfloat/multilingual-e5-small`) |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY_B64URL`, `VAPID_SUBJECT` | Web Push |
| `CORS_ORIGINS`, `UPLOAD_DIR`, `LANGUAGE` | Εφαρμογή |
| `PHOTO_RETENTION_DAYS`, `REPEAT_MIN_GAP_HOURS` | Πολιτικές |

---

## Tests

```bash
# Unit (state machine + SLA/silence)
python -m pytest backend/tests/test_state_machine.py backend/tests/test_closed_silence.py -q

# Isolation smoke tests (χρειάζεται τρέχον server)
RXTRAIL_TEST_BASE=http://127.0.0.1:8000 python -m pytest backend/tests/test_isolation_smoke.py -q
```

Τα smoke tests ελέγχουν: κλειστή εγγραφή, admin authz, απομόνωση γιατρών/περιστατικών/γνώσης
ανά org, global sharing (Κοινός Χώρος + chat), αλλαγή κωδικού.

---

## Deployment (production)

Παράδειγμα: Debian/Ubuntu + systemd + nginx (reverse proxy σε `127.0.0.1:8002`).

```bash
# server
mkdir -p /var/www/rxtrail
# αντίγραψε backend/ frontend/ και δημιούργησε .env με τα πραγματικά κλειδιά

python3 -m venv /var/www/rxtrail/.venv   # ή system-wide (βλ. σχόλιο στο service)
pip install -r backend/requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu

cp rxtrail.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rxtrail

cp nginx-rxtrail.conf /etc/nginx/sites-available/rxtrail
ln -s /etc/nginx/sites-available/rxtrail /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# nightly backup (mongodump + uploads, 14ήμερο retention)
crontab -e   # 0 3 * * * /var/www/rxtrail/backup.sh >> /var/log/rxtrail-backup.log 2>&1
```

- **Backups**: [`backup.sh`](backup.sh) — mongodump + uploads, αυτόματη διατήρηση 14 ημερών.
- **Service**: [`rxtrail.service`](rxtrail.service) — `HF_HOME` σε εγγράψιμο path για το τοπικό μοντέλο.
- **Nginx**: [`nginx-rxtrail.conf`](nginx-rxtrail.conf) — static frontend + `/api` proxy.
- Οι φωτογραφίες γνωματεύσεων διαγράφονται αυτόματα μετά το retention (scheduler).

---

## Ασφάλεια & απομόνωση δεδομένων

- **Org isolation**: κάθε query φιλτράρεται με `org_id`. Τα smoke tests το επιβεβαιώνουν.
- **Κλειστή εγγραφή**: `ALLOW_OPEN_REGISTRATION=false` — μόνο ο admin δημιουργεί λογαριασμούς.
- **Admin-only**: `/admin/*` ελέγχει `is_admin` ή `ADMIN_EMAIL`.
- **Passwords**: bcrypt · **JWT** για sessions · rate limit στο login (5 αποτυχίες / 10′).
- **PII scrubbing** πριν από εξωτερικά APIs (LLM/embeddings) — ΑΜΚΑ, τηλέφωνα, ημερομηνίες.
- **Αρχεία**: τα uploads δεν σερβίρονται δημόσια με directory listing· γίνεται serve μέσω endpoints με auth.
- **Backups**: δεν περιέχουν κλειδιά (μόνο DB + uploads) και μένουν στον server.

> ⚠️ Μην ανεβάζεις ποτέ `.env`, κλειδιά API, ή dumps δεδομένων. Το `.gitignore` τα καλύπτει.

---

## Ο Ασκληπιός — AI βοηθός

| Δυνατότητα | Περιγραφή |
|---|---|
| Πλήρες context | Σε κάθε ερώτηση βλέπει γιατρούς+σημειώσεις, δομές, πηγές, περιστατικά, ραντεβού |
| Συνδυασμός | RAG γνώση + κατάλογος SKU + σημειώσεις σε μία απάντηση |
| Case-insensitive αναζήτηση | Accent/case normalization (π.χ. «πατρικαλακης» → «ΠΑΤΡΙΚΑΛΑΚΗΣ») |
| Ψαχτήρι γνώσης | Τοπικά embeddings + cosine ranking (χωρίς εξωτερικό reranker) |
| Ημερήσιο πλάνο | «οργάνωσέ μου την ημέρα» → πρόταση με ώρες → «ναι» → δημιουργία ραντεβού |
| Εβδομαδιαίο πλάνο | Δευτέρα–Παρασκευή, σεβασμός σημειώσεων («μόνο Πέμπτη»), ομοιόμορφη κατανομή |
| Μνήμη | Working memory + σύνοψη παλιών συνομιλιών + επεισοδιακή μνήμη (ραντεβού, σημειώσεις) |
| Ασφάλεια αριθμών | Απαγορεύεται να επινοεί αριθμούς/κωδικούς — μόνο από εργαλεία/δεδομένα |

---

## API (σύνοψη)

Όλα τα endpoints κάτω από `/api` (production) ή απευθείας (local), με `Authorization: Bearer <JWT>`.

```
POST   /auth/login                 POST /auth/change-password     GET /auth/me
GET    /cases?q=…&status=…         POST /cases                     GET /cases/{id}
PATCH  /cases/{id}                 POST /cases/{id}/transition     POST /cases/{id}/repeat
POST   /cases/{id}/note            POST /cases/{id}/add-prescription
POST   /cases/{id}/handover
GET    /appointments/week|month    POST /appointments              POST /appointments/{id}/note
POST   /prescriptions/from-photo   GET  /prescriptions/ocr-status/{job}
GET    /week/proposal              POST /week/lock|unlock
GET    /knowledge/files            POST /knowledge/upload          GET /knowledge/search?q=
GET    /share/files|folders        POST /share/upload              GET/POST /share/chat/messages
GET/POST /doctors /structures /sources /skus /contacts
GET    /notifications/push/public-key   POST /notifications/push/{subscribe|test}
POST   /llm/intake                 POST /llm/assess                POST /llm/agent
GET    /admin/users                GET  /admin/users/{id}/stats    GET /admin/users/{id}/export.xlsx
GET    /admin/users/{id}/week.xlsx GET  /admin/export.xlsx
```

---

## Συνεισφορά / Σημειώσεις

- **Κώδικας**: Python (FastAPI) + Vanilla JS (ES modules), χωρίς build step.
- **Στυλ**: χρησιμοποίησε τα υπάρχοντα tokens (`frontend/tokens.css`) — όχι hardcoded χρώματα.
- **Tests**: τρέξε τα παραπάνω πριν από κάθε αλλαγή σε state machine, auth ή isolation.
- **Μυστικά**: ποτέ σε κώδικα ή commits — πάντα `.env` στον server.
- **License**: εσωτερικό/ιδιόκτητο έργο. Επικοινώνησε με τον συγγραφέα πριν από οποιαδήποτε
  αναδιανομή.

---

**RxTrail** · Φτιαγμένο στην Κρήτη 🇬🇷
