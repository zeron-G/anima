import os
base = r'D:\data\code\github\anima\anima'
files = []
for root, dirs, fnames in os.walk(base):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for f in fnames:
        if f.endswith('.py'):
            rel = os.path.join(root, f).replace(base + os.sep, '')
            files.append(rel)
print(f'Total .py files: {len(files)}')
for f in sorted(files):
    if any(x in f for x in ['network', 'evolution', 'channel', 'gossip', 'node', 'sync', 'session', 'remote']):
        print(' ', f)
