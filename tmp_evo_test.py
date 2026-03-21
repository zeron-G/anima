import sys
sys.path.insert(0, '.')
from anima.evolution.proposal import create_proposal
p = create_proposal(type='feature', title='test', problem='p', solution='s', files=['anima/main.py'], risk='low', priority=3, complexity='small')
print('proposal:', p.id, p.title)
