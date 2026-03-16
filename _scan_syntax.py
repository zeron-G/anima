import ast, os

errors = []
checked = 0
base = r'D:\data\code\github\anima\anima'
for root, dirs, files in os.walk(base):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for fname in files:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(root, fname)
        checked += 1
        try:
            ast.parse(open(fpath, 'r', encoding='utf-8').read())
        except SyntaxError as e:
            errors.append(f'SYNTAX: {fpath}: {e}')
        except Exception as e:
            errors.append(f'ERR: {fpath}: {e}')

print(f'Checked {checked} files')
for e in errors:
    print(e)
if not errors:
    print('No syntax errors found')
