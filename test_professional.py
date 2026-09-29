import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('OPENAI_API_KEY', 'test-key')
os.environ.setdefault('TWILIO_ACCOUNT_SID', 'AC' + '0' * 32)
os.environ.setdefault('TWILIO_AUTH_TOKEN', 'test-token')
os.environ.setdefault('DASHBOARD_USERNAME', 'admin')
os.environ.setdefault('DASHBOARD_PASSWORD', 'test-password')

import main


class ProfessionalPlatformTests(unittest.TestCase):
    def setUp(self):
        with main.db() as conn:
            for table in ('messages', 'conversations', 'processed_messages', 'projects',
                          'audit_events', 'notifications'):
                conn.execute(f'DELETE FROM {table}')
        self.client = main.app.test_client()
        self.auth = {'Authorization': 'Basic YWRtaW46dGVzdC1wYXNzd29yZA=='}
        self.phone = 'whatsapp:+34600999888'

    def test_knowledge_search_uses_only_approved_sources(self):
        results = main.store.search_knowledge('green room camerino maquillaje vestuario', 5)
        self.assertTrue(results)
        self.assertTrue(all(x['status'] == 'approved' for x in results))
        self.assertIn('Green Rooms', results[0]['title'])

    def test_complex_project_creates_high_value_room(self):
        project = main.store.update_project_from_message(
            self.phone,
            'Rodaje en Madrid con dos zonas de green room: 15 personas de agencia y 6 futbolistas, '
            'video village, maquillaje, vestuario, climatización, transporte y montaje.'
        )
        self.assertEqual(project['high_value'], 1)
        self.assertGreaterEqual(len(project['services']), 3)
        self.assertEqual(main.store.conversation(self.phone)['priority'], 'HIGH')

    def test_human_send_requires_explicit_takeover(self):
        main.ensure_conversation(self.phone)
        response = self.client.post('/dashboard/api/send', headers=self.auth,
                                    json={'phone': self.phone, 'body': 'Respuesta humana'})
        self.assertEqual(response.status_code, 409)

    @patch.object(main, 'notify_owner')
    @patch.object(main.openai_client.chat.completions, 'create')
    def test_unsafe_ai_commercial_commitment_is_blocked(self, create, notify):
        create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='Sí, está disponible por 900 € y queda confirmado.'))])
        response = self.client.post('/whatsapp', data={
            'From': self.phone, 'Body': 'Necesito información sobre vuestro servicio de Wi-Fi',
            'MessageSid': 'SM-safety'
        })
        self.assertNotIn('900', response.text)
        self.assertEqual(main.get_state(self.phone), main.WAITING_FOR_HUMAN)
        notify.assert_called_once()

    def test_dashboard_exposes_projects_knowledge_and_audit(self):
        self.assertEqual(self.client.get('/dashboard/api/projects', headers=self.auth).status_code, 200)
        knowledge = self.client.get('/dashboard/api/knowledge', headers=self.auth)
        self.assertEqual(knowledge.status_code, 200)
        self.assertGreaterEqual(len(knowledge.get_json()), 10)
        self.assertEqual(self.client.get('/dashboard/api/audit', headers=self.auth).status_code, 200)

    def test_legacy_restaurant_surface_is_closed(self):
        response = self.client.get('/get-reservations')
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()['status'], 'disabled')


if __name__ == '__main__':
    unittest.main()
