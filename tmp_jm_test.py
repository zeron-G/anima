import jmcomic
import json
import traceback

try:
    option = jmcomic.JmOption.default()
    cl = option.new_jm_client()
    sp = cl.search_site('love', page=1)
    
    with open(r'D:\data\code\github\anima\tmp_jm_result.txt', 'w', encoding='utf-8') as f:
        f.write(f'type: {type(sp)}\n')
        f.write(f'repr: {repr(sp)}\n')
        try:
            items = list(sp)
            f.write(f'items count: {len(items)}\n')
            for i, item in enumerate(items[:5]):
                f.write(f'  [{i}] {item}\n')
        except Exception as e:
            f.write(f'iter error: {e}\n')
            f.write(traceback.format_exc())
except Exception as e:
    with open(r'D:\data\code\github\anima\tmp_jm_result.txt', 'w', encoding='utf-8') as f:
        f.write(f'ERROR: {e}\n')
        f.write(traceback.format_exc())
