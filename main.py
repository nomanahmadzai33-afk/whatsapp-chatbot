import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from datetime import datetime
import pytz

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

def get_madrid_time():
    madrid_tz = pytz.timezone('Europe/Madrid')
    return datetime.now(madrid_tz)

def get_system_prompt():
    now = get_madrid_time()
    current_date = now.strftime("%A, %B %d, %Y")
    current_time = now.strftime("%I:%M %p")

    return f"""You are an intelligent AI assistant for COCI Marketing, a premium restaurant marketing agency based in Madrid, Spain.

CURRENT DATE AND TIME:
- Today is: {current_date}
- Current time: {current_time}

LANGUAGE: Detect instantly. Spanish → native Spanish. English → native English. Default: Spanish.

PERSONALITY: Professional, warm, confident. Maximum 2-3 sentences per response. Never robotic. No markdown. Never say you are an AI.

ABOUT COCI MARKETING:
- Website: cocimarketing.com
- Phone: +34 637 048 572
- Email: cocimarketing6@gmail.com
- Address: Conde Rodríguez San Pedro Street 58, Madrid, Spain
- Speciality: Social-first marketing exclusively for restaurants and cafés

SERVICES & PRICING:
- COCI LITE 590€/month: 4 posts/month IG+TikTok, 1 Reel, Google Business basic, monthly report
- COCI STARTER 990€/month: 8 posts/month, 2 Reels, content shoot quarterly, WhatsApp support
- COCI GROWTH 2.490€/month: Ads on 2 channels, 1.000€ ad spend included, 16 posts, 4 Reels, Google Maps strategy, review automation
- Custom packages available

INDIVIDUAL SERVICES: Social Media Management, Google Maps SEO, Paid Media Campaigns, Food Photography & Reels, Brand Identity, Website Development, WhatsApp Automation, UGC & Influencer Campaigns, Growth Strategy.

LEAD QUALIFICATION — ask naturally one by one:
1. What type of restaurant/café do they have?
2. Where are they located?
3. What is their main challenge? (more customers, visibility, social media, reservations)
4. Do they currently work with a marketing agency?
5. What is their approximate monthly marketing budget?

After qualifying say:
ES: "Perfecto, con esta información nuestro equipo puede preparar una propuesta personalizada. ¿Cuándo os viene bien una llamada gratuita de 15 minutos?"
EN: "Perfect, our team can prepare a personalized proposal. When would a free 15-minute call work for you?"

BOOK CONSULTATION: Collect name, best time/day, phone or email.
Confirm: "Genial [nombre], nuestro equipo os contactará el [día]. ¡Hasta pronto!"

STRICT RULES:
- Never mention any platform or technology
- Never invent services or prices
- Always warm and professional
- Keep answers short — restaurant owners are busy
- If complex question: "Nuestro equipo puede explicaros esto en detalle en la consulta gratuita." """

conversation_history = {}

@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')

        if sender not in conversation_history:
            conversation_history[sender] = []

        conversation_history[sender].append({'role': 'user', 'content': incoming_msg})

        response = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'system', 'content': get_system_prompt()}] + conversation_history[sender],
            max_tokens=300
        )
        reply = response.choices[0].message.content
        conversation_history[sender].append({'role': 'assistant', 'content': reply})

    except Exception as e:
        print(f"Error: {e}")
        reply = 'Lo sentimos, por favor intenta de nuevo.'

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return 'COCI Marketing WhatsApp Bot running!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
