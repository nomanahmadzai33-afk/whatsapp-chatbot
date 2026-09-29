import os
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
test_db.close()
os.environ.update({
    'OPENAI_API_KEY': 'test-key',
    'TWILIO_ACCOUNT_SID': 'AC' + '0' * 32,
    'TWILIO_AUTH_TOKEN': 'test-token',
    'DASHBOARD_USERNAME': 'admin',
    'DASHBOARD_PASSWORD': 'test-password',
    'DB_PATH': test_db.name,
})

import main


class TakeoverTests(unittest.TestCase):
    def setUp(self):
        with main.db() as conn:
            conn.execute('DELETE FROM messages')
            conn.execute('DELETE FROM conversations')
            conn.execute('DELETE FROM processed_messages')
            conn.execute('DELETE FROM projects')
            conn.execute('DELETE FROM audit_events')
            conn.execute('DELETE FROM notifications')
        self.client = main.app.test_client()
        self.auth = {'Authorization': 'Basic YWRtaW46dGVzdC1wYXNzd29yZA=='}
        self.phone = 'whatsapp:+34600111222'

    @patch.object(main, 'notify_owner')
    @patch.object(main, 'save_purocuento_lead', return_value=True)
    def test_quote_request_waits_for_human_and_ai_stays_silent(self, _save, notify):
        first = self.client.post('/whatsapp', data={
            'From': self.phone, 'Body': 'Necesito un presupuesto', 'MessageSid': 'SM1'
        })
        self.assertIn('equipo de PuroCuento', first.text)
        self.assertEqual(main.get_state(self.phone), main.WAITING_FOR_HUMAN)
        notify.assert_called_once()

        second = self.client.post('/whatsapp', data={
            'From': self.phone, 'Body': '¿Hola?', 'MessageSid': 'SM2'
        })
        self.assertNotIn('<Message>', second.text)

    def test_takeover_release_and_auth(self):
        main.ensure_conversation(self.phone)
        self.assertEqual(self.client.get('/dashboard').status_code, 401)
        take = self.client.post('/dashboard/api/state', headers=self.auth,
                                json={'phone': self.phone, 'state': main.HUMAN_ACTIVE})
        self.assertEqual(take.status_code, 200)
        self.assertEqual(main.get_state(self.phone), main.HUMAN_ACTIVE)
        release = self.client.post('/dashboard/api/state', headers=self.auth,
                                   json={'phone': self.phone, 'state': main.AI_ACTIVE})
        self.assertEqual(release.status_code, 200)
        self.assertEqual(main.get_state(self.phone), main.AI_ACTIVE)

    @patch.object(main, 'notify_owner')
    def test_every_new_message_while_waiting_notifies_owner(self, notify):
        main.ensure_conversation(self.phone)
        main.set_state(self.phone, main.WAITING_FOR_HUMAN, 'ai', 'Briefing cualificado')
        response = self.client.post('/whatsapp', data={
            'From': self.phone, 'Body': 'También necesitamos acceso de carga', 'MessageSid': 'SM-waiting-followup'
        })
        self.assertNotIn('<Message>', response.text)
        notify.assert_called_once_with(self.phone, 'También necesitamos acceso de carga',
                                       'Nuevo mensaje en conversación derivada', force=True)

    def test_failed_notification_clears_deduplication_for_retry(self):
        main.ensure_conversation(self.phone)
        data = main.store.conversation(self.phone)
        data['notified_at'] = main.now_iso()
        main.store.upsert_conversation(**data)
        main.store.save_notification('SM-notify-failed', self.phone, 'whatsapp:+34691582624', 'queued')
        response = self.client.post('/twilio/message-status', data={
            'MessageSid': 'SM-notify-failed', 'MessageStatus': 'undelivered', 'ErrorCode': '63016'
        })
        self.assertEqual(response.status_code, 204)
        self.assertEqual(main.store.conversation(self.phone)['notified_at'], '')

    def test_complex_green_room_prompt_is_consultative_not_speaker_led(self):
        prompt = main.get_system_prompt()
        self.assertIn('MODO CONSULTOR PARA PROYECTOS COMPLEJOS', prompt)
        self.assertIn('No empieces recomendando un producto aislado', prompt)
        self.assertIn('No introduzcas altavoces en una consulta general de Green Room', prompt)
        self.assertIn('visita técnica', prompt)

    @patch.object(main.twilio_client.messages, 'create')
    def test_human_reply_uses_twilio_and_owns_thread(self, create):
        main.ensure_conversation(self.phone)
        main.set_state(self.phone, main.HUMAN_ACTIVE, 'Equipo PuroCuento')
        create.return_value = SimpleNamespace(sid='SM-human', status='queued')
        response = self.client.post('/dashboard/api/send', headers=self.auth,
                                    json={'phone': self.phone, 'body': 'Hola, soy Laura del equipo.'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs['from_'], main.TWILIO_WHATSAPP_NUMBER)
        self.assertEqual(create.call_args.kwargs['to'], self.phone)
        self.assertEqual(create.call_args.kwargs['body'], 'Hola, soy Laura del equipo.')
        self.assertIn('status_callback', create.call_args.kwargs)
        self.assertEqual(main.get_state(self.phone), main.HUMAN_ACTIVE)

    @patch.object(main, 'notify_owner')
    @patch.object(main, 'save_purocuento_lead', return_value=True)
    def test_completed_qualified_lead_notifies_without_price_keyword(self, _save, notify):
        main.save_history(self.phone, [
            {'role': 'user', 'content': 'Necesito alquilar un altavoz para un evento de 50 personas'},
            {'role': 'assistant', 'content': '¿Puedes facilitarme un contacto?'},
        ])
        response = self.client.post('/whatsapp', data={
            'From': self.phone, 'Body': 'charli@purocuento.es 615672154',
            'MessageSid': 'SM-qualified'
        })
        self.assertIn('revisión del equipo', response.text)
        self.assertEqual(main.get_state(self.phone), main.WAITING_FOR_HUMAN)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], self.phone)

    @patch.object(main, 'notify_owner')
    @patch.object(main, 'save_purocuento_lead', return_value=True)
    @patch.object(main.openai_client.chat.completions, 'create')
    def test_ai_cannot_promise_human_contact_without_real_handoff(self, create, _save, notify):
        create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='Nuestro equipo se pondrá en contacto contigo para continuar.'))])
        response = self.client.post('/whatsapp', data={
            'From': self.phone, 'Body': 'Tengo una consulta especial', 'MessageSid': 'SM-promise'
        })
        self.assertIn('equipo se pondrá en contacto', response.text)
        self.assertEqual(main.get_state(self.phone), main.WAITING_FOR_HUMAN)
        notify.assert_called_once()


if __name__ == '__main__':
    unittest.main()
