import os
import unittest

os.environ.setdefault('OPENAI_API_KEY', 'test-key')
os.environ.setdefault('TWILIO_ACCOUNT_SID', 'AC' + '0' * 32)
os.environ.setdefault('TWILIO_AUTH_TOKEN', 'test-token')

from main import is_greeting


class GreetingDetectionTests(unittest.TestCase):
    def test_real_greetings(self):
        for message in ('Hi', 'Hello', 'Hola', 'Buenos días', 'Buenas tardes'):
            with self.subTest(message=message):
                self.assertTrue(is_greeting(message))

    def test_substrings_are_not_greetings(self):
        for message in (
            'Which models do you offer?',
            'This speaker',
            'Shipping information',
        ):
            with self.subTest(message=message):
                self.assertFalse(is_greeting(message))


if __name__ == '__main__':
    unittest.main()
