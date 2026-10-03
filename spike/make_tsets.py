"""Derive expected affected-address sets for T1-T5 and S7 directly from the public spec
(dev/change_tests.json expected_behavior + brief), using only the address table.
Assumption (flagged): postal_city == legal municipality for NJ/MA/CA rows; Boston neighborhoods -> Boston."""
import csv, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
rows = list(csv.DictReader(open(ROOT/'data/sample_addresses.csv')))
BOSTON = {'Boston','Dorchester','Roxbury','East Boston','Brighton','Allston','South Boston','Jamaica Plain','Hyde Park','Mattapan'}
def city(r):
    c = r['postal_city']
    if c in BOSTON: return 'Boston'
    if c == 'San Ysidro': return 'San Diego'
    return c
ids = lambda f: sorted(r['address_id'] for r in rows if f(r))
camb = lambda r: city(r)=='Cambridge' and r['units'].strip()
out = {
 'T1': {'affected': ids(lambda r: r['state']=='CA'), 'conflict': []},
 'T2': {'affected_HOB': ids(lambda r: city(r)=='Hoboken'), 'affected_JC': ids(lambda r: city(r)=='Jersey City'),
        'affected': ids(lambda r: city(r) in ('Hoboken','Jersey City')), 'must_exclude': ids(lambda r: city(r)=='Newark')},
 'T3': {'affected': ids(lambda r: r['state']=='NJ'), 'conflict': ids(lambda r: city(r) in ('Hoboken','Jersey City'))},
 'T4': {'affected': ids(lambda r: r['state']=='MA'), 'conflict': []},
 'T5': {'affected': [], 'conflict': []},
 'S7A': {'affected': ids(lambda r: camb(r) and int(r['units'])>=6)},
 'S7B': {'affected': ids(lambda r: camb(r) and int(r['units'])>6)},
}
json.dump(out, open(ROOT/'spike/expected_tsets.json','w'), indent=1)
print({k: len(v['affected']) for k,v in out.items()}, 'T3 conflict', len(out['T3']['conflict']))
