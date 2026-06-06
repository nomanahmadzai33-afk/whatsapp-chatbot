import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from datetime import datetime
import pytz
import gspread
from google.oauth2 import service_account
import base64

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
twilio_client = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))

def get_sheets_client():
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            return None
        try:
            creds_info = json.loads(base64.b64decode(creds_json).decode())
        except:
            creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        )
        return gspread.authorize(credentials)
    except Exception as e:
        print(f"Sheets error: {e}")
        return None

def save_reservation(name, date, time_str, guests, phone):
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        sheet = gc.open("La Penela Reservations").sheet1
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, guests, date, time_str, phone, "CONFIRMED", ""])
        return True
    except Exception as e:
        print(f"Sheets save error: {e}")
        return False

def send_sms(phone, name, guests, date, time_str):
    try:
        twilio_client.messages.create(
            body=f"Hola {name}! Tu reserva en La Penela Moraleja: {guests} personas el {date} a las {time_str}. Te llamaremos el dia anterior para confirmar. Tel: 916 505 232",
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{phone}"
        )
        return True
    except Exception as e:
        print(f"SMS error: {e}")
        return False

def get_madrid_time():
    madrid_tz = pytz.timezone('Europe/Madrid')
    return datetime.now(madrid_tz)

def get_system_prompt():
    now = get_madrid_time()
    current_date = now.strftime("%A, %d de %B de %Y")
    current_time = now.strftime("%H:%M")
    return f"""Eres el asistente virtual de WhatsApp del restaurante La Penela Moraleja en Madrid.

FECHA Y HORA ACTUAL: {current_date}, {current_time} (Madrid)

IDIOMA: Español nativo por defecto. Si escriben en inglés responde en inglés. Si en francés, en francés.

PERSONALIDAD: Cálido, profesional, humano. Máximo 2-3 frases. Sin markdown. Nunca digas que eres IA.

INFO:
- Restaurante: La Penela Moraleja
- Dirección: Calle Estafeta 2, Plaza de la Fuente, La Moraleja, Alcobendas, Madrid
- Teléfono: 916 505 232
- Horario: Lunes-Domingo, Comidas 13:00-18:00, Cenas 20:00-01:00
- Cerrado domingos por la noche
- Precio medio: 70€ persona
- Delivery: UberEats + recogida

MENÚ DESTACADO:
- Tortilla de Betanzos Grande 18,50€ / Pequeña 9€
- Pulpo á Feira 22€
- Croquetas (8u) 13,60€
- Ternera Asada La Penela 24€
- Merluza del Pincho 26€
- Rape Negro 28€
- Callos a la Gallega 16,50€
- Postres 6,90€

RESERVAS:
Recoge uno a uno: nombre, día completo con fecha (ej: domingo 8 de junio), hora, personas, teléfono.
Tras los 5 datos confirma:
"Perfecto [nombre], reserva para [n] personas el [día y fecha] a las [hora]. Recibirás confirmación por mensaje ahora mismo y te llamaremos el día anterior. ¡Hasta pronto!"

Luego escribe SOLO en nueva línea:
SAVE_RESERVATION:name=NOMBRE|date=FECHA|time=HORA|guests=NUMERO|phone=TELEFONO

REGLAS:
- Nunca confirmar 100% — el equipo confirma
- Nunca inventar info
- Siempre cálido y profesional"""

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
        if 'SAVE_RESERVATION:' in reply:
            try:
                data = reply.split('SAVE_RESERVATION:')[1].split('\n')[0].strip()
                parts = dict(p.split('=', 1) for p in data.split('|'))
                save_reservation(parts.get('name',''), parts.get('date',''), parts.get('time',''), parts.get('guests',''), parts.get('phone',''))
                send_sms(parts.get('phone',''), parts.get('name',''), parts.get('guests',''), parts.get('date',''), parts.get('time',''))
                reply = reply.replace(f"SAVE_RESERVATION:{data}", '').strip()
            except Exception as e:
                print(f"Reservation error: {e}")
    except Exception as e:
        print(f"Error: {e}")
        reply = 'Lo sentimos, intenta de nuevo.'
    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return 'La Penela Moraleja WhatsApp Bot running!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
