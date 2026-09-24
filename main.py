import os
import json
import logging
import re
from flask_cors import CORS
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from datetime import datetime
import pytz
import gspread
from google.oauth2 import service_account
import base64

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=[
    "https://talkserveai.net",
    "https://www.talkserveai.net",
    "https://*.railway.app"
])

openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
twilio_client  = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))

# ── Google Sheets helper ──────────────────────────────────────────────────────
SHEET_NAME = "Du Liban Reservations"

def get_sheets_client():
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            logger.warning("GOOGLE_CREDENTIALS env var not set")
            return None
        try:
            creds_info = json.loads(base64.b64decode(creds_json).decode())
        except Exception:
            creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
        )
        return gspread.authorize(credentials)
    except Exception as e:
        logger.error(f"Sheets client error: {e}")
        return None

def save_purocuento_lead(name, company, phone_email, project_type, services, dates, location, people, logistics, description):
    """Save PuroCuento enquiry to 'PuroCuento Leads' worksheet. Auto-creates if missing."""
    try:
        gc = get_sheets_client()
        if not gc:
            logger.warning("Sheets client unavailable; escalation logged only")
            return False
        try:
            sheet = gc.open(SHEET_NAME).worksheet("PuroCuento Leads")
        except Exception:
            spreadsheet = gc.open(SHEET_NAME)
            sheet = spreadsheet.add_worksheet(title="PuroCuento Leads", rows=1000, cols=12)
            sheet.append_row(["Timestamp", "Name", "Company", "Phone/Email", "ProjectType", "Services", "Dates", "Location", "People", "Logistics", "Description", "Status"])
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, company, phone_email, project_type, services, dates, location, people, logistics, description, "PENDING"])
        logger.info(f"PuroCuento lead saved: {name} from {company}")
        return True
    except Exception as e:
        logger.error(f"save_purocuento_lead error: {e}")
        return False

def save_reservation(name, date, time_str, guests, phone):
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        sheet = gc.open(SHEET_NAME).sheet1
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, guests, date, time_str, phone, "CONFIRMED", ""])
        logger.info(f"Reservation saved: {name} — {date} {time_str} ({guests} guests)")
        return True
    except Exception as e:
        logger.error(f"save_reservation error: {e}")
        return False

def save_order(name, phone, items, pickup_time, pickup_date, total):
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        try:
            sheet = gc.open(SHEET_NAME).worksheet("Pedidos")
        except Exception:
            spreadsheet = gc.open(SHEET_NAME)
            sheet = spreadsheet.add_worksheet(title="Pedidos", rows=1000, cols=10)
            sheet.append_row(["Timestamp","Name","Phone","Items","PickupDate","PickupTime","Total","Status","Notes"])
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, phone, items, pickup_date, pickup_time, total, "PENDING", ""])
        logger.info(f"Order saved: {name}")
        return True
    except Exception as e:
        logger.error(f"save_order error: {e}")
        return False

def send_whatsapp_confirmation(phone, name, guests, date, time_str):
    """Send a WhatsApp confirmation message to the guest."""
    try:
        twilio_number = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
        if guests:
            body = (
                f"✅ Hola {name}! Tu reserva en Du Liban está confirmada: "
                f"{guests} persona(s) el {date} a las {time_str}. "
                f"Te llamaremos el día anterior para confirmar. ¡Hasta pronto! 📞 +34 911 677 963"
            )
        else:
            body = (
                f"✅ Hola {name}! Tu reserva en Du Liban está anotada para el {date} a las {time_str}. "
                f"Te confirmaremos en breve. ¡Hasta pronto! 📞 +34 911 677 963"
            )
        twilio_client.messages.create(
            body=body,
            from_=twilio_number,
            to=f"whatsapp:{phone}"
        )
        logger.info(f"WhatsApp confirmation sent to {phone}")
        return True
    except Exception as e:
        logger.error(f"send_whatsapp_confirmation error: {e}")
        return False

# ── Handoff detection ──────────────────────────────────────────────────────────
HANDOFF_KEYWORDS_ES = [r'precio', r'presupuesto', r'costo', r'tarifa', r'disponibilidad', r'disponible', r'stock', r'reserva', r'urgent', r'queja', r'contrato', r'descuento', r'entrega', r'quiero hablar']
HANDOFF_KEYWORDS_EN = [r'price', r'quote', r'cost', r'rate', r'availability', r'available', r'stock', r'reservation', r'urgent', r'complaint', r'contract', r'discount', r'delivery', r'i want to speak']

HANDOFF_MESSAGE_ES = "Gracias por tu interés. Para precio, disponibilidad, presupuesto o reserva, necesito pasar tu solicitud al equipo de PuroCuento. Voy a recopilar los datos básicos de tu proyecto y el equipo te responderá con una propuesta personalizada."
HANDOFF_MESSAGE_EN = "Thank you for your interest. For pricing, availability, quotes, or reservations, I need to pass your request to the PuroCuento team. I will collect your project details and they will respond with a personalized proposal."

def detect_language(text):
    """Simple language detection: English or Spanish (default)."""
    en_words = ['hello', 'hi', 'what', 'how', 'can', 'need', 'do', 'is', 'the', 'you', 'your', 'want', 'price']
    return 'en' if sum(1 for w in en_words if w in text.lower()) >= 2 else 'es'

def check_handoff(text, language):
    """Return handoff message if keywords detected, else None."""
    keywords = HANDOFF_KEYWORDS_EN if language == 'en' else HANDOFF_KEYWORDS_ES
    text_lower = text.lower()
    for pattern in keywords:
        if re.search(pattern, text_lower):
            return HANDOFF_MESSAGE_EN if language == 'en' else HANDOFF_MESSAGE_ES
    return None

# ── Time helpers ──────────────────────────────────────────────────────────────
def get_madrid_time():
    madrid_tz = pytz.timezone('Europe/Madrid')
    return datetime.now(madrid_tz)

def get_kitchen_status(now):
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    time_mins = hour * 60 + minute

    if weekday == 0:
        return "RESTAURANTE_CERRADO_LUNES"
    if time_mins < 13 * 60 or time_mins >= 25 * 60:
        return "RESTAURANTE_CERRADO_HORARIO"
    if 16 * 60 <= time_mins < 20 * 60:
        return "COCINA_CERRADA_TARDE"
    if time_mins >= 23 * 60 + 30:
        return "COCINA_CERRADA_NOCHE"
    return "SERVICIO_COMPLETO"

# ── System prompt ─────────────────────────────────────────────────────────────
def get_system_prompt():
    now = get_madrid_time()
    current_date = now.strftime("%A, %d de %B de %Y")
    current_time = now.strftime("%H:%M")
    kitchen_status = get_kitchen_status(now)

    if kitchen_status == "RESTAURANTE_CERRADO_LUNES":
        kitchen_notice = "⚠️ ESTADO ACTUAL: El restaurante está CERRADO hoy (lunes). Los lunes Du Liban no abre.\nNo hagas ninguna reserva para hoy."
    elif kitchen_status == "RESTAURANTE_CERRADO_HORARIO":
        kitchen_notice = "⚠️ ESTADO ACTUAL: El restaurante está CERRADO ahora mismo.\nHorario: martes a domingo de 13:00 a 01:00."
    elif kitchen_status == "COCINA_CERRADA_TARDE":
        kitchen_notice = "⚠️ ESTADO ACTUAL (16:00–20:00): La cocina está CERRADA.\nSólo shisha, cócteles y bebidas."
    elif kitchen_status == "COCINA_CERRADA_NOCHE":
        kitchen_notice = "⚠️ ESTADO ACTUAL (después de 23:30): La cocina ha cerrado.\nSólo bebidas y shisha."
    else:
        kitchen_notice = "✅ ESTADO ACTUAL: Servicio completo activo."

    return f"""Eres el asistente virtual de WhatsApp del restaurante Du Liban de la Moraleja en Alcobendas, Madrid. No tienes nombre propio. Eres cálido, profesional y completamente humano. Nunca suenes como robot o IA.

FECHA Y HORA ACTUAL (Madrid): {current_date}, {current_time}

{kitchen_notice}

────────────────────────────────────
IDIOMA
────────────────────────────────────
Detecta el idioma del cliente al instante y mantén ese mismo idioma durante toda la conversación.
- Español → español nativo de España
- Inglés → inglés nativo
- Por defecto: español

TONO: Cálido, cercano y profesional. Máximo 2–3 frases por respuesta. Natural, nunca robótico.

────────────────────────────────────
INFORMACIÓN DEL RESTAURANTE
────────────────────────────────────
Nombre: Du Liban de la Moraleja
Tipo: Restaurante libanés
Dirección: Alcobendas (La Moraleja), Madrid, España
Teléfono: +34 911 677 963
Web: talkserveai.net

HORARIO:
- Martes a domingo: 13:00 – 01:00
- LUNES: CERRADO

────────────────────────────────────
CARTA (resumen)
────────────────────────────────────
MEZZE FRÍOS: Hummus, Moutabal, Tabbouleh, Fattoush, Warak Enab, Muhammara, Labneh
MEZZE CALIENTES: Falafel, Kafta, Kebbeh, Hallumi, Soujok, Batata Harrah, Rakakat Jebneh
PRINCIPALES: Taouk (pollo), Kafta Meshwiyye, Lahmeh Mshwieh (ternera), Samak Mashwi (pescado), Farrouj (pollo entero), Mawzet Kharouf (codillo de cordero)
MARISCOS: Okhtobut (pulpo), Salata Al Rubyan (langostinos)
POSTRES: Knefeh, Baklava, Mhalabieh, Aish El Saraya, Em Ali
SHISHA / NARGUILE: Disponible (30€)
BEBIDAS: Cócteles, Mojito (13€), Margarita (13€), Aperol Spritz (14€)

Precio medio por persona: 35–55€

────────────────────────────────────
RESERVAS
────────────────────────────────────
Recoge los datos UNO A UNO de forma natural:
1. Nombre completo del cliente
2. Día y fecha completa
3. Hora
4. Número de personas
5. Teléfono de contacto

NUNCA confirmes reservas para LUNES. NUNCA prometas comida fuera de horario (16:00–20:00 o después de 23:30).

────────────────────────────────────
REGLAS ESTRICTAS
────────────────────────────────────
1. NUNCA inventes información sobre precios o disponibilidad
2. NUNCA digas que eres una IA o un bot
3. Respuestas cortas siempre (máximo 3 frases)
4. El equipo humano confirma definitivamente; tú sólo anotas
"""

# ── Conversation history ──────────────────────────────────────────────────────
MAX_HISTORY = 50
CONV_DIR = "/tmp/conversations"
os.makedirs(CONV_DIR, exist_ok=True)

def _conv_path(sender: str) -> str:
    safe = sender.replace("+", "plus").replace(":", "_").replace("/", "_")
    return os.path.join(CONV_DIR, f"{safe}.json")

def get_history(sender: str) -> list:
    path = _conv_path(sender)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(sender: str, history: list):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    path = _conv_path(sender)
    with open(path, "w") as f:
        json.dump(history, f)

def trim_history(sender: str):
    pass

# ── WhatsApp webhook ──────────────────────────────────────────────────────────
@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')

        logger.info(f"Message from {sender}: {incoming_msg[:80]}")

        language = detect_language(incoming_msg)
        handoff = check_handoff(incoming_msg, language)

        if handoff:
            logger.info(f"Handoff triggered for {sender} ({language})")
            save_purocuento_lead("", "", "", "", "", "", "", "", "", incoming_msg)
            reply = handoff
        else:
            history = get_history(sender)
            history.append({'role': 'user', 'content': incoming_msg})

            response = openai_client.chat.completions.create(
                model='gpt-4o',
                messages=[{'role': 'system', 'content': get_system_prompt()}] + history,
                max_tokens=450,
                temperature=0.7
            )
            reply = response.choices[0].message.content.strip()
            history.append({'role': 'assistant', 'content': reply})
            save_history(sender, history)

            # ── SAVE_RESERVATION disabled for PuroCuento ──
            if 'SAVE_RESERVATION:' in reply:
                logger.warning("SAVE_RESERVATION command ignored (PuroCuento demo)")
                reply = reply.split('SAVE_RESERVATION:')[0].strip()

            # ── SAVE_ORDER disabled for PuroCuento ──
            if 'SAVE_ORDER:' in reply:
                logger.warning("SAVE_ORDER command ignored (PuroCuento demo)")
                reply = reply.split('SAVE_ORDER:')[0].strip()

        logger.info(f"Reply to {sender}: {reply[:80]}")

    except Exception as e:
        logger.error(f"whatsapp() error: {e}")
        reply = 'Lo sentimos, ha habido un problema. Por favor inténtalo de nuevo o llámanos al +34 911 677 963.'

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

# ── Voice receptionist reservation endpoint ───────────────────────────────────
@app.route('/voice-reservation', methods=['POST'])
def voice_reservation():
    try:
        data = request.get_json()
        name = data.get('name', '')
        date = data.get('date', '')
        time_str = data.get('time', '')
        guests = data.get('guests', '')
        phone = data.get('phone', '')
        if phone and not phone.startswith('+'):
            phone = '+34' + phone
        save_reservation(name, date, time_str, guests, phone)
        send_whatsapp_confirmation(phone, name, guests, date, time_str)
        logger.info(f"Voice reservation saved: {name}")
        return {'status': 'success', 'message': f'Reservation saved for {name}'}, 200
    except Exception as e:
        logger.error(f"voice_reservation error: {e}")
        return {'status': 'error', 'message': str(e)}, 500

# ── IVR reservation confirmation (Twilio voice callback) ─────────────────────
@app.route('/confirm-reservation', methods=['POST'])
def confirm_reservation():
    digit = request.values.get('Digits', '')
    phone = (
        request.values.get('phone', '')
        or request.values.get('To', '')
        or request.values.get('Called', '')
    )
    try:
        gc = get_sheets_client()
        sheet = gc.open(SHEET_NAME).sheet1
        records = sheet.get_all_records()
        msg = "No encontramos su reserva. Llámenos al 911 677 963."
        for i, r in enumerate(records):
            r_phone = ''.join(ch for ch in str(r.get('Phone', '')) if ch.isdigit())
            c_phone = ''.join(ch for ch in str(phone) if ch.isdigit())
            if len(r_phone) < 9 or len(c_phone) < 9:
                continue
            if r_phone[-9:] == c_phone[-9:]:
                row_num = i + 2
                if digit == '1':
                    sheet.update_cell(row_num, 7, 'VERIFIED')
                    msg = "Gracias, su reserva en Du Liban ha sido verificada. Le esperamos."
                elif digit == '2':
                    sheet.update_cell(row_num, 7, 'CANCELLED')
                    msg = "Su reserva ha sido cancelada. Si necesita ayuda llámenos al 911 677 963."
                else:
                    msg = "No hemos recibido su respuesta. Por favor llámenos al 911 677 963."
                break
    except Exception as e:
        logger.error(f"confirm_reservation error: {e}")
        msg = "Ha ocurrido un error. Por favor llámenos al 911 677 963."

    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response><Say language="es-ES" voice="Polly.Conchita">{msg}</Say></Response>'''
    return twiml, 200, {'Content-Type': 'text/xml'}

# ── Data read endpoints (for dashboard) ───────────────────────────────────────
@app.route('/get-reservations', methods=['GET'])
def get_reservations():
    try:
        gc = get_sheets_client()
        sheet = gc.open(SHEET_NAME).sheet1
        records = sheet.get_all_records()
        return {'reservations': records}, 200
    except Exception as e:
        logger.error(f"get_reservations error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-orders', methods=['GET'])
def get_orders():
    try:
        gc = get_sheets_client()
        try:
            sheet = gc.open(SHEET_NAME).worksheet("Pedidos")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'orders': records}, 200
    except Exception as e:
        logger.error(f"get_orders error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-customers', methods=['GET'])
def get_customers():
    try:
        gc = get_sheets_client()
        try:
            sheet = gc.open(SHEET_NAME).worksheet("Customers")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'customers': records}, 200
    except Exception as e:
        logger.error(f"get_customers error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-tables', methods=['GET'])
def get_tables():
    try:
        gc = get_sheets_client()
        try:
            sheet = gc.open(SHEET_NAME).worksheet("Tables")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'tables': records}, 200
    except Exception as e:
        logger.error(f"get_tables error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-pending-actions', methods=['GET'])
def get_pending_actions():
    try:
        gc = get_sheets_client()
        try:
            sheet = gc.open(SHEET_NAME).worksheet("PendingActions")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'actions': records}, 200
    except Exception as e:
        logger.error(f"get_pending_actions error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-table', methods=['POST'])
def update_table():
    try:
        data = request.get_json()
        table_id = data.get('table_id')
        status = data.get('status')
        guest = data.get('guest_name', '')
        gc = get_sheets_client()
        try:
            sheet = gc.open(SHEET_NAME).worksheet("Tables")
            records = sheet.get_all_records()
            for i, r in enumerate(records):
                if str(r.get('TableID', '')) == str(table_id):
                    sheet.update_cell(i + 2, 2, status)
                    sheet.update_cell(i + 2, 3, guest)
                    return {'status': 'success'}, 200
            sheet.append_row([table_id, status, guest])
        except Exception:
            pass
        return {'status': 'ok'}, 200
    except Exception as e:
        logger.error(f"update_table error: {e}")
        return {'error': str(e)}, 500

@app.route('/resolve-pending-action', methods=['POST'])
def resolve_pending_action():
    try:
        data = request.get_json()
        row_num = data.get('row')
        gc = get_sheets_client()
        sheet = gc.open(SHEET_NAME).worksheet("PendingActions")
        headers = sheet.row_values(1)
        status_col = headers.index('Estado') + 1
        sheet.update_cell(row_num, status_col, 'HECHO')
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"resolve_pending_action error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-reservation', methods=['POST'])
def update_reservation():
    try:
        data = request.get_json()
        row_num = data.get('row')
        field = data.get('field')
        value = data.get('value')
        gc = get_sheets_client()
        sheet = gc.open(SHEET_NAME).sheet1
        headers = sheet.row_values(1)
        col = headers.index(field) + 1
        sheet.update_cell(row_num, col, value)
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"update_reservation error: {e}")
        return {'error': str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)

