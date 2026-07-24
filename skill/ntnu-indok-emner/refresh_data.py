#!/usr/bin/env python3
"""Full refresh: discover + extract every continent from the NTNU wiki,
merge into data/data_all.json and rebuild index.html.

Usage:
  python refresh_data.py
"""
import json, os, subprocess, sys
import ntnu_indok as ni

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(BASE, '..', '..'))
DATA_DIR = os.path.join(ROOT, 'data')
CACHE_DIR = os.path.join(BASE, 'raw')

# (name, root_page_id, filename slug) — slugs match the existing data/*.json naming
CONTINENTS = [
    ('Europa', '101092381', 'europe'),
    ('Asia', '107250332', 'asia'),
    ('Amerika', '107250743', 'amerika'),
    ('Afrika', '101093756', 'afrika'),
    ('Australia/NZ', '107250722', 'australia_nz'),
    ('Canada', '146607325', 'canada'),
]

def main():
    all_rows = []
    for name, root, slug in CONTINENTS:
        print(f'== {name} ({root}) ==')
        catalog = ni.discover(root, name)
        n_uni = sum(len(c['universities']) for c in catalog['countries'])
        cat_path = os.path.join(DATA_DIR, f'universities_{slug}.json')
        json.dump(catalog, open(cat_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'  discovered {n_uni} universiteter -> {cat_path}')

        rows, stats = ni.extract(cat_path, cache_dir=CACHE_DIR)
        for r in rows:
            r['continent'] = name
        data_path = os.path.join(DATA_DIR, f'data_{slug}.json')
        json.dump(rows, open(data_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'  extracted {len(rows)} rader -> {data_path}')
        for country, uni, n in stats:
            if isinstance(n, str):
                print(f'    FEIL  {country} / {uni}: {n}')
        all_rows.extend(rows)

    all_path = os.path.join(DATA_DIR, 'data_all.json')
    json.dump(all_rows, open(all_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'TOTAL {len(all_rows)} rader -> {all_path}')

    subprocess.run([sys.executable, os.path.join(BASE, 'build_app.py')], check=True)

if __name__ == '__main__':
    main()
