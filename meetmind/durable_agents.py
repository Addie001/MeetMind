"""Versioned local checkpoints for the read-only dual-agent subgraph."""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from langgraph.checkpoint.sqlite import SqliteSaver
from . import agents, core


def analyze(paragraphs, meeting_date, checkpoint_path):
    # Input, model and implementation version are part of the checkpoint key.
    # Old checkpoints are retained for inspection but never reused after a change.
    import os
    version = {'paragraphs':paragraphs,'meeting_date':meeting_date,'model':os.getenv('LLM_MODEL'),
               'endpoint':os.getenv('LLM_BASE_URL'),
               'agents_sha256':hashlib.sha256(Path(agents.__file__).read_bytes()).hexdigest(),
               'core_sha256':hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest()}
    thread=hashlib.sha256(json.dumps(version,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    config={'configurable':{'thread_id':thread},'run_name':'MeetMind durable evidence review'}
    with closing(sqlite3.connect(checkpoint_path,check_same_thread=False,timeout=30)) as conn:
        graph=agents.build_graph(SqliteSaver(conn))
        snapshot=graph.get_state(config)
        if snapshot.values and not snapshot.next:
            return snapshot.values['report']
        state=None if snapshot.values else {'paragraphs':paragraphs,'meeting_date':meeting_date,'max_repairs':1}
        return graph.invoke(state,config)['report']
