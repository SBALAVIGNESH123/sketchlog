import json
data = json.load(open('jobs.json', encoding='utf-16'))
for j in data['jobs']:
    print(f"{j['name']}: {j['id']} ({j['conclusion']})")
