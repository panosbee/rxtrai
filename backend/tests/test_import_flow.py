"""Quick test: import structures from Excel."""
import httpx, asyncio, io, openpyxl, json

async def test():
    # Create test Excel with structures
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Structures'
    ws.append(['Ονομα', 'Τυπος', 'Πολη', 'Νομος', 'Τηλεφωνο'])
    ws.append(['Νοσοκομείο Ευαγγελισμός', 'HOSPITAL', 'Αθήνα', 'Αττικής', '2107201000'])
    ws.append(['Κέντρο Υγείας Ηρακλείου', 'HEALTH_CENTER', 'Ηράκλειο', 'Ηρακλείου', '2810123456'])
    ws.append(['Γηροκομείο Παπαδοπούλου', 'NURSING_HOME', 'Χανιά', 'Χανίων', '2821098765'])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    async with httpx.AsyncClient(timeout=30) as c:
        # Login
        r = await c.post('http://127.0.0.1:8000/auth/login', json={'email':'test@rxtrail.gr','password':'123456'})
        token = r.json()['access_token']
        h = {'Authorization': f'Bearer {token}'}

        # Analyze
        buf.seek(0)
        r = await c.post('http://127.0.0.1:8000/imports/analyze?kind=STRUCTURES', files={'file': ('test.xlsx', buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}, headers=h)
        data = r.json()
        print('Analyze OK — headers:', data.get('headers'), 'rows:', data.get('total_rows'))
        job_id = data['import_job_id']

        # Set mapping
        mapping = {'Ονομα': 'name', 'Τυπος': 'type', 'Πολη': 'city', 'Νομος': 'prefecture', 'Τηλεφωνο': 'phone'}
        r = await c.post('http://127.0.0.1:8000/imports/preview', json={'mapping': mapping, 'import_job_id': job_id}, headers=h)
        print('Preview:', r.json().get('status'))

        # Commit
        r = await c.post('http://127.0.0.1:8000/imports/commit', json={'import_job_id': job_id}, headers=h)
        report = r.json()
        print('Commit report:', json.dumps(report, ensure_ascii=False, indent=2))

        # Verify structures
        r = await c.get('http://127.0.0.1:8000/structures?limit=10', headers=h)
        structs = r.json()
        print('Structures in DB:', structs['total'])
        for s in structs.get('items', []):
            print(f'  - {s.get("name")} ({s.get("type")}) — {s.get("city")}')

asyncio.run(test())
