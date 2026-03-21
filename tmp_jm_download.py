import jmcomic
import traceback

try:
    option = jmcomic.JmOption.default()
    cl = option.new_jm_client()
    
    # Search for something nice
    sp = cl.search_site('彼女', page=1)
    items = list(sp)
    
    with open(r'D:\data\code\github\anima\tmp_jm_search_full.txt', 'w', encoding='utf-8') as f:
        f.write(f'Total: {len(items)} results\n\n')
        for i, item in enumerate(items[:10]):
            f.write(f'[{i}] JM{item[0]} - {item[1]}\n')

except Exception as e:
    with open(r'D:\data\code\github\anima\tmp_jm_search_full.txt', 'w', encoding='utf-8') as f:
        f.write(f'ERROR: {e}\n')
        f.write(traceback.format_exc())
