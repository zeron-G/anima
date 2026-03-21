import jmcomic
import json
import traceback

try:
    option = jmcomic.JmOption.default()
    cl = option.new_jm_client()
    
    # Try different search terms
    for query in ['漫画', '甜', '巨乳', '女', 'manga']:
        sp = cl.search_site(query, page=1)
        items = list(sp)
        with open(r'D:\data\code\github\anima\tmp_jm_result2.txt', 'a', encoding='utf-8') as f:
            f.write(f'query={query}: {len(items)} results\n')
            for i, item in enumerate(items[:3]):
                f.write(f'  [{i}] {item}\n')
        if items:
            break

except Exception as e:
    with open(r'D:\data\code\github\anima\tmp_jm_result2.txt', 'a', encoding='utf-8') as f:
        f.write(f'ERROR: {e}\n')
        f.write(traceback.format_exc())
