"""Durable StateGraph. All external writes live behind a static approval boundary."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from contextlib import contextmanager, closing
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from . import integrations
from .core import task_payload


class MeetingState(TypedDict, total=False):
    meeting_id: str
    approved_ids: list[str]
    target_project: str
    has_actions: bool
    has_pending: bool


class MeetingWorkflow:
    def __init__(self, checkpoint_path, get, save, lock, analyze, create_task=None):
        self.path, self.get, self.save, self.lock = checkpoint_path, get, save, lock
        self.analyze = analyze
        self.create_task = create_task or integrations.create_task

    def fetch(self, state):
        with self.lock:
            m = self.get(state['meeting_id'])
        if m.get('source', {}).get('type') == 'tencent' and not m['paragraphs']:
            paragraphs = integrations.fetch_transcripts(m['source'])
            with self.lock:
                m = self.get(m['id'])
                m['paragraphs'] = paragraphs
                self.save(m)
        elif not m['paragraphs']:
            raise ValueError('没有可分析的转写')
        return {}

    def extract(self, state):
        with self.lock:
            m = self.get(state['meeting_id'])
        # If a crash occurred after saving the report, do not overwrite review data.
        if not m['report']:
            report = self.analyze(m['paragraphs'], m['date'])
            with self.lock:
                m = self.get(m['id'])
                m.update(report=report, status='review', error='')
                self.save(m)
        return {'has_actions': bool(m['report']['actions'])}

    def review_gate(self, state):
        return {'approved_ids': [], 'target_project': ''}

    def sync(self, state):
        with self.lock, integrations.tool_schema_cache():
            m = self.get(state['meeting_id'])
            actions = self.validate_selection(m, state.get('approved_ids', []), state.get('target_project'))
            payloads = [task_payload(a, m, state['target_project']) for a in actions]
            for a, payload in zip(actions, payloads):
                a['status'] = 'syncing'
                self.save(m)
                try:
                    result = self.create_task(payload)
                    if not result.get('id'):
                        raise ValueError('服务未返回任务 ID')
                    a.update(status='synced', external_id=result['id'], sync_error='')
                except integrations.NotSentError as exc:
                    a.update(status='pending', sync_error=str(exc))
                except Exception:
                    a.update(status='uncertain', sync_error='未能确认写入结果，请到滴答清单核实；为避免重复，已禁止自动重试。')
                self.save(m)
            return {'has_pending': any(a['status'] == 'pending' for a in m['report']['actions']),
                    'approved_ids': [], 'target_project': ''}

    @staticmethod
    def validate_selection(m, ids, project):
        if not isinstance(project, str) or not project.strip():
            raise ValueError('请选择目标清单')
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError('无效行动项 ID')
        actions = [a for a in m['report']['actions'] if a['id'] in ids] if m['report'] else []
        if (not actions or len(actions) != len(ids) or len(ids) != len(set(ids)) or
                any(not a['confirmed'] or not a['selected'] or a['status'] != 'pending' for a in actions)):
            raise ValueError('只能同步已确认、已勾选且未同步的行动项')
        return actions

    @contextmanager
    def graph(self):
        with closing(sqlite3.connect(self.path, check_same_thread=False)) as conn:
            builder = StateGraph(MeetingState)
            builder.add_node('fetch_transcripts', self.fetch)
            builder.add_node('extract_actions', self.extract)
            builder.add_node('review_gate', self.review_gate)
            builder.add_node('sync_tasks', self.sync)
            builder.add_edge(START, 'fetch_transcripts')
            builder.add_edge('fetch_transcripts', 'extract_actions')
            builder.add_conditional_edges('extract_actions', lambda s: 'review' if s['has_actions'] else 'end',
                                          {'review': 'review_gate', 'end': END})
            builder.add_edge('review_gate', 'sync_tasks')
            builder.add_conditional_edges('sync_tasks', lambda s: 'review' if s['has_pending'] else 'end',
                                          {'review': 'review_gate', 'end': END})
            yield builder.compile(checkpointer=SqliteSaver(conn), interrupt_before=['sync_tasks'])

    @staticmethod
    def config(mid):
        return {'configurable': {'thread_id': mid}, 'run_name': 'MeetMind meeting workflow'}

    def start(self, mid):
        with self.graph() as graph:
            config = self.config(mid)
            snapshot = graph.get_state(config)
            # Retry failures from checkpoints rather than redo successful extraction.
            graph.invoke(None if snapshot.values else {'meeting_id': mid}, config)
            return graph.get_state(config).next

    def resume(self, mid, ids, project):
        with self.lock, self.graph() as graph:
            config = self.config(mid)
            self.validate_selection(self.get(mid), ids, project)
            snapshot = graph.get_state(config)
            if not snapshot.values:
                # Import reports from the previous version without running extraction again.
                graph.update_state(config, {'meeting_id': mid, 'has_actions': True}, as_node='extract_actions')
                graph.invoke(None, config)
                snapshot = graph.get_state(config)
            if snapshot.next != ('sync_tasks',):
                raise ValueError('流程未停在人工确认节点，不能写入')
            graph.update_state(config, {'approved_ids': ids, 'target_project': project}, as_node='review_gate')
            graph.invoke(None, config)
            return graph.get_state(config).next

    def resolve_uncertain(self, mid, action_id, resolution):
        """Record a human decision locally; this method never calls a remote tool."""
        with self.lock:
            m = self.get(mid)
            actions = m['report']['actions'] if m.get('report') else []
            action = next((a for a in actions if a['id'] == action_id), None)
            if not action or action['status'] != 'uncertain':
                raise ValueError('只能核实待核实状态的任务')
            if resolution not in ('created', 'not_created'):
                raise ValueError('无效核实结果')
            if any(a['status'] == 'syncing' for a in actions):
                raise ValueError('会议正在同步，请完成后再核实')
            action.setdefault('verification_history', []).append({
                'verified_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
                'method': 'manual', 'resolution': resolution,
                'previous_sync_error': action.get('sync_error', ''), 'topic': action['topic'],
            })
            if resolution == 'created':
                # A manual confirmation does not invent an external task ID.
                action.update(status='synced', sync_error='')
            else:
                action.update(status='pending', sync_error='', external_id=None)
                # A batch containing only uncertain items may already have ended.
                # Re-arm its approval boundary without invoking any graph node.
                with self.graph() as graph:
                    config = self.config(mid)
                    if not graph.get_state(config).next:
                        graph.update_state(config, {'meeting_id': mid, 'has_actions': True,
                                                    'approved_ids': [], 'target_project': ''},
                                           as_node='review_gate')
            self.save(m)
            return m

    def forget(self, mid):
        """Remove only this meeting's durable checkpoints and pending writes."""
        if Path(self.path).exists():
            with closing(sqlite3.connect(self.path, check_same_thread=False)) as conn:
                SqliteSaver(conn).delete_thread(mid)

    def pending(self, mid):
        with self.graph() as graph:
            return graph.get_state(self.config(mid)).next
