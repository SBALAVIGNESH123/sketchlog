import json
data = json.load(open('jobs_new.json', encoding='utf-16'))
for j in data['jobs']:
    print(f"{j['name']}: {j['id']} ({j['conclusion']})")
