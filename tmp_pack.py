import zipfile
from pathlib import Path

d = Path(r'D:\data\jmcomic\1411659')
zip_path = Path(r'D:\data\jmcomic\JM1411659.zip')

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
    for f in sorted(d.rglob('*')):
        if f.is_file():
            zf.write(f, f.relative_to(d))

size_mb = zip_path.stat().st_size / 1024 / 1024
with open(r'D:\data\code\github\anima\tmp_pack_result.txt', 'w') as out:
    out.write(f'OK: {zip_path} ({size_mb:.1f} MB)\n')
