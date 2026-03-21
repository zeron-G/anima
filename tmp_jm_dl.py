import jmcomic
import traceback
from pathlib import Path

DEST = r'D:\data\jmcomic'

try:
    album_id = '1411659'
    option = jmcomic.JmOption.default()
    option.dir_rule = jmcomic.DirRule('Bd_Aid', DEST)
    
    print(f'Downloading JM{album_id}...')
    jmcomic.download_album(album_id, option)
    
    # Check result
    for d in Path(DEST).iterdir():
        if d.is_dir() and album_id in d.name:
            files = list(d.rglob("*.webp")) + list(d.rglob("*.jpg")) + list(d.rglob("*.png"))
            print(f'Done: {d} ({len(files)} images)')
            break
    else:
        print(f'Done: check {DEST}')
        
except Exception as e:
    print(f'ERROR: {e}')
    traceback.print_exc()
