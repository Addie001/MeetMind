import sqlite3
import unittest
from meetmind import server
import test_workflow


class MeetingDeleteTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown

    def delete(self, mid, token=server.TOKEN):
        response = self.client.delete('/api/meetings/' + mid, headers={'X-MeetMind-Token': token})
        return response.status_code, response.json()

    def test_delete_report_and_checkpoints_preserves_other_meeting(self):
        other=server.create_meeting({'title':'保留会议','transcript':'[00:01] 甲：我负责提交文档。'})
        server.run_analysis(other['id'])
        self.assertEqual(self.delete(self.m['id'])[0],200)
        with self.assertRaises(ValueError):server.get(self.m['id'])
        self.assertEqual(server.get(other['id'])['title'],'保留会议')
        with sqlite3.connect(str(server.DB)+'.checkpoints') as db:
            for table in ('checkpoints','writes'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE thread_id=?',(self.m['id'],)).fetchone()[0],0)
            self.assertGreater(db.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?',(other['id'],)).fetchone()[0],0)

    def test_delete_last_meeting_returns_empty_state(self):
        self.assertEqual(self.delete(self.m['id'])[0],200)
        self.assertEqual(self.client.get('/api/state').json()['meetings'], [])
        self.assertEqual(self.delete(self.m['id'])[0],404)

    def test_delete_requires_valid_request_token(self):
        self.assertEqual(self.delete(self.m['id'],token='invalid')[0],403)
        self.assertEqual(server.get(self.m['id'])['id'],self.m['id'])

    def test_cannot_delete_active_background_analysis(self):
        m=server.get(self.m['id']);m['status']='analyzing';server.save(m)
        self.assertEqual(self.delete(m['id'])[0],409)
        m['status']='review';server.save(m)
        server.RUNNING_ANALYSES.add(m['id'])
        try:
            self.assertEqual(self.delete(m['id'])[0],409)
        finally:
            server.RUNNING_ANALYSES.discard(m['id'])
        self.assertEqual(self.delete(m['id'])[0],200)

    def test_can_delete_unanalyzed_and_synced_local_records(self):
        manual=server.create_meeting({'title':'导入错误','transcript':'原始转写'})
        self.assertEqual(self.delete(manual['id'])[0],200)
        m=server.get(self.m['id']);m['report']['actions'][0].update(status='synced',external_id='remote-retained')
        server.save(m)
        self.assertEqual(self.delete(m['id'])[0],200)
