import glob

def clean(f):
    content = open(f, encoding='utf-8').read()
    lines = content.splitlines()
    cleaned = '\n'.join(line.rstrip() for line in lines) + '\n'
    open(f, 'w', encoding='utf-8', newline='').write(cleaned)

clean('python/sketchlog/__init__.py')
clean('tests/test_features.py')
