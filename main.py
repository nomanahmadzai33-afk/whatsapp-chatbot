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
            body=f"✅ Hola {name}! Tu reserva en La Penela Moraleja está confirmada: {guests} personas el {date} a las {time_str}. Te llamaremos el día anterior para confirmar. ¡Hasta pronto! 📞 916 505 232",
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
    return f"""Eres el asistente virtual de WhatsApp del restaurante La Penela Moraleja en Madrid. No tienes nombre. Eres cálido, profesional y completamente humano. Nunca suenes como robot o IA. Nunca digas que eres IA.

FECHA Y HORA ACTUAL (Madrid): {current_date}, {current_time}

IDIOMA: Detecta al instante y mantén ese idioma toda la conversación.
- Español → español nativo
- Inglés → inglés nativo
- Francés → francés nativo
- Por defecto: español

TONO: Cálido y profesional. Máximo 2-3 frases por respuesta. Natural, nunca robótico.

INFO DEL RESTAURANTE:
- Nombre: La Penela Moraleja
- Dirección: Calle Estafeta 2, Plaza de la Fuente, La Moraleja, Alcobendas, Madrid
- Teléfono: 916 505 232
- Fundado: 1989 en Betanzos, A Coruña
- Horario: Lunes-Domingo, Comidas 13:00-18:00, Cenas 20:00-01:00
- Cerrado domingos por la noche
- Espacios: salón interior, barra, terraza delantera (verano), terraza interior (cerrada)
- Aforo: 300 personas, hasta 400 días especiales
- Precio medio: 70€ por persona
- Delivery: UberEats + recogida en local
- Eventos: reservas-moraleja@lapenela.com

MENÚ ENTRANTES:
- Tortilla de Betanzos Grande 18,50€ / Pequeña 9,00€
- Empanada Gallega 11,50€ / 38,50€ / 71,50€
- Ensaladilla Rusa
- Croquetas Caseras (8u) 13,60€
- Zamburiñas
- Pulpo á Feira con Cachelos 22,00€
- Pulpo a la Gallega con Almejas
- Almejas a la Marinera
- Salpicón de Rape y Marisco
- Caldo Gallego

MENÚ PRINCIPALES:
- Ternera Asada La Penela 24,00€
- Entrecot a la Parrilla
- Solomillo
- Carne Asada a Baja Temperatura
- Merluza del Pincho 26,00€
- Rape Negro 28,00€
- Pescado fresco diario: rodaballo, lubina, besugo, merluza
- Callos a la Gallega 16,50€

POSTRES 6,90€: Filloas, Leche Frita, Tarta de Santiago, Tarta de Queso

VINOS:
- Barallobre Albariño 17,00€
- Pétalos del Bierzo 28,00€
- Allende Rioja 29,50€
- Valduero Crianza 27,00€

RESERVAS — MUY IMPORTANTE:
Recoge uno a uno: nombre, día completo con fecha (ej: domingo 8 de junio), hora, número de personas, teléfono.
Tras los 5 datos confirma exactamente así:
"Perfecto [nombre], reserva para [n] personas el [día y fecha completa] a las [hora]. Recibirás confirmación por mensaje ahora mismo y te llamaremos el día anterior para confirmar. ¡Hasta pronto!"

Luego escribe SOLO en nueva línea:
SAVE_RESERVATION:name=NOMBRE|date=FECHA|time=HORA|guests=NUMERO|phone=TELEFONO

GRUPOS +20 personas: reservas-moraleja@lapenela.com

CAMBIOS Y CANCELACIONES:
Pide nombre, fecha original, nueva fecha. Confirma con día+fecha+mes completo.

PEDIDOS PARA RECOGER:
Recoge uno a uno: nombre, teléfono, fecha de recogida (día+fecha+mes completo), hora de recogida (solo 13:00-18:00 o 20:00-01:00), artículos uno a uno.
Lee el pedido completo con total al final.
Di: "Su pedido para el [día] [fecha] de [mes] a las [hora]. Le llamaremos el día anterior para confirmar."

UBEREATS: "Búscanos en UberEats como La Penela Moraleja."

FACTURAS: Recoge nombre empresa, CIF, email. Respuesta en 24h.

TRANSFERENCIA A HUMANO: "Ahora te paso con nuestro equipo."

FECHA CRITICA: Cuando el cliente mencione una fecha, SIEMPRE calcula el dia de la semana correcto matematicamente. Hoy es Sunday 07 de June de 2026. Nunca inventes el dia de la semana.

REGLAS ESTRICTAS:
- Siempre confirmar fechas con día+fecha+mes completo
- Nunca confirmar al 100% — el equipo confirma
- Nunca inventar información
- Respuestas cortas siempre"""

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
            max_tokens=400
        )
        reply = response.choices[0].message.content
        conversation_history[sender].append({'role': 'assistant', 'content': reply})
        if 'SAVE_RESERVATION:' in reply:
            try:
                data = reply.split('SAVE_RESERVATION:')[1].split('\n')[0].strip()
                parts = dict(p.split('=', 1) for p in data.split('|'))
                save_reservation(parts.get('name',''), parts.get('date',''), parts.get('time',''), parts.get('guests',''), parts.get('phone',''))
                send_sms(sender.replace('whatsapp:',''), parts.get('name',''), parts.get('guests',''), parts.get('date',''), parts.get('time',''))
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
