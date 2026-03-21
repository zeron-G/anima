import sys, asyncio
sys.path.insert(0, '.')

async def main():
    from anima.evolution.proposal import create_proposal
    from anima.evolution.engine import EvolutionEngine
    
    engine = EvolutionEngine()
    
    proposal = create_proposal(
        type='feature',
        title='node_state idle/load sync to gossip broadcasts',
        problem='_periodic_network_tasks() in anima/main.py never updates node_state.idle_score, node_state.idle_level, or node_state.current_load. These fields are always 0.0/busy in gossip broadcasts, so remote nodes cannot make informed task delegation decisions based on actual load.',
        solution='''In _periodic_network_tasks() in anima/main.py, after the split_brain.check() call, add 4 lines to sync idle_scheduler state to node_state and bump version.

File: anima/main.py
Old string (exact):
            # Split-brain check
            split_brain.check(gossip_mesh.get_alive_count())
            # Sync with random alive peer

New string (exact):
            # Split-brain check
            split_brain.check(gossip_mesh.get_alive_count())
            # Sync node_state load metrics so remote nodes see real load
            _idle_sched = heartbeat_deps.get("idle_scheduler")
            if _idle_sched is not None:
                node_state.idle_score = _idle_sched.score
                node_state.idle_level = _idle_sched.level
                node_state.bump_version()
            # Sync with random alive peer''',
        files=['anima/main.py'],
        risk='low',
        priority=3,
        complexity='small',
    )
    
    result = await engine.submit_proposal(proposal)
    print('Result:', result)
    print('Proposal ID:', proposal.id)
    print('Status:', proposal.status)

asyncio.run(main())
