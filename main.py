import os
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

SYSTEM_PROMPT = 'You are a helpful AI assistant for restaurants using TalkServe AI. Keep responses concise and friendly for WhatsApp. No markdown.'

conversation_history = {}

@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    incoming_msg = request.values.get('Body', '').strip()
    sender = request.values.get('From', '')
    if sender not in conversation_history:
        conversation_history[sender] = []
    conversation_history[sender].append({'role': 'user', 'content': incoming_msg})
    try:
        response = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'system', 'content': SYSTEM_PROMPT}] + conversation_history[sender],
            max_tokens=300
        )
        reply = response.choices[0].message.content
        conversation_history[sender].append({'role': 'assistant', 'content': reply})
    except:
        reply = 'Sorry, please try again!'
    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return 'WhatsApp Chatbot running!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
