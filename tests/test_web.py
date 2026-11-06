"""HTTP boundary and startup regression tests for the FastAPI migration."""
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from meetmind import server
import test_workflow


class WebTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown

    def test_invalid_json_and_typed_fields_return_frontend_error(self):
        headers = {'X-MeetMind-Token': server.TOKEN, 'Content-Type': 'application/json'}
        for body in ('{', '[]', '{"meeting_id": 123}'):
            response = self.client.post('/api/analyze', content=body, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertIn('error', response.json())

    def test_host_and_body_size_protection(self):
        self.assertEqual(self.client.get('/api/state', headers={'Host': 'attacker.example'}).status_code, 400)
        response = self.client.post('/api/meetings', content='x' * 2_000_001)
        self.assertEqual(response.status_code, 400)
        self.assertIn('2MB', response.json()['error'])

    def test_partial_review_preserves_confirmation(self):
        headers = {'X-MeetMind-Token': server.TOKEN}
        body = {'meeting_id': self.m['id'], 'action_id': self.a['id'], 'confirmed': True}
        self.assertEqual(self.client.post('/api/review', json=body, headers=headers).status_code, 200)
        body.pop('confirmed')
        body['selected'] = True
        response = self.client.post('/api/review', json=body, headers=headers)
        action = response.json()['report']['actions'][0]
        self.assertTrue(action['confirmed'])
        self.assertTrue(action['selected'])

    def test_lifespan_recovers_interrupted_operations(self):
        m = server.get(self.m['id'])
        m['status'] = 'analyzing'
        m['report']['actions'][0]['status'] = 'syncing'
        server.save(m)
        # Start against the existing temporary database, never the real data folder.
        with patch.object(server, 'ROOT', Path(self.tmp.name)), patch.object(server, 'DB', server.DB):
            data = Path(self.tmp.name) / 'data'
            data.mkdir()
            target = data / 'meetmind.sqlite3'
            target.write_bytes(server.DB.read_bytes())
            with TestClient(server.app, base_url='http://127.0.0.1') as client:
                meeting = client.get('/api/state').json()['meetings'][0]
                self.assertEqual(meeting['status'], 'error')
                self.assertEqual(meeting['report']['actions'][0]['status'], 'uncertain')

    def test_openapi_describes_request_models(self):
        schema = self.client.get('/openapi.json').json()
        self.assertIn('ReviewRequest', schema['components']['schemas'])
        self.assertIn('/api/sync', schema['paths'])
