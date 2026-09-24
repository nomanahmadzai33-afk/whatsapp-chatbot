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
    "https://purocuento.es",
    "https://www.purocuento.es",
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
    """Legacy endpoint stub - not called by PuroCuento demo."""
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        sheet = gc.open(SHEET_NAME).sheet1
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, guests, date, time_str, phone, "CONFIRMED", ""])
        logger.info(f"Legacy reservation saved: {name}")
        return True
    except Exception as e:
        logger.error(f"save_reservation error: {e}")
        return False

def save_order(name, phone, items, pickup_time, pickup_date, total):
    """Legacy endpoint stub - not called by PuroCuento demo."""
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
        logger.info(f"Legacy order saved: {name}")
        return True
    except Exception as e:
        logger.error(f"save_order error: {e}")
        return False

def send_whatsapp_confirmation(phone, name, guests, date, time_str):
    """Legacy endpoint stub - not called by PuroCuento demo."""
    pass

# ── Handoff detection ──────────────────────────────────────────────────────────
HANDOFF_KEYWORDS_ES = [r'precio', r'presupuesto', r'costo', r'tarifa', r'disponibilidad', r'disponible', r'stock', r'reserva', r'urgent', r'queja', r'contrato', r'descuento', r'entrega', r'quiero hablar']
HANDOFF_KEYWORDS_EN = [r'price', r'quote', r'cost', r'rate', r'availability', r'available', r'stock', r'reservation', r'urgent', r'complaint', r'contract', r'discount', r'delivery', r'i want to speak']

HANDOFF_MESSAGE_ES = "Gracias por tu interés. Para precio, disponibilidad, presupuesto o reserva, necesito pasar tu solicitud al equipo de PuroCuento. Voy a recopilar los datos básicos de tu proyecto y el equipo te responderá con una propuesta personalizada."
HANDOFF_MESSAGE_EN = "Thank you for your interest. For pricing, availability, quotes, or reservations, I need to pass your request to the PuroCuento team. I will collect your project details and they will respond with a personalized proposal."

GREETING_RESPONSE_ES = "Hola, soy el asistente virtual de PuroCuento. Puedo ayudarte con información general sobre nuestros servicios de producción audiovisual y recogida de datos de proyectos. Para precios, disponibilidad o presupuestos, te pondré en contacto con nuestro equipo. ¿En qué puedo ayudarte?"
GREETING_RESPONSE_EN = "Hello, I'm PuroCuento's AI assistant. I can help with general information about our audiovisual production services and project data collection. For pricing, availability, or quotes, I'll connect you with our team. How can I help?"

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

def is_greeting(text):
    """Check if message is a simple greeting."""
    greetings_es = [r'hola', r'buenos', r'hi', r'hello', r'hey']
    greetings_en = [r'hello', r'hi', r'hey', r'greetings']
    text_lower = text.lower().strip()
    all_greetings = greetings_es + greetings_en
    return any(re.search(pattern, text_lower) for pattern in all_greetings)

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

# ── PuroCuento System Prompt ──────────────────────────────────────────────────
def get_system_prompt():
    return """Eres el asistente virtual de WhatsApp de PuroCuento, empresa madrileña especializada en servicios de producción audiovisual y de eventos. Eres profesional, conciso y completamente humano. Nunca suenes como robot o IA. Nunca digas que eres IA.

────────────────────────────────────
INFORMACIÓN DE PUROCUENTO
────────────────────────────────────
Nombre: PuroCuento
Tipo: Empresa de servicios de producción audiovisual y de eventos
Ubicación: Madrid, España. Servicios en toda España.
Teléfono/WhatsApp: +34 657 654 417
Email: operativa@purocuento.es
Web: https://purocuento.es

HORARIO:
- Lunes a viernes: 09:00–14:00 y 16:00–18:00 (hora de Madrid)
- Sábado/Domingo: CERRADO

SERVICIOS PRINCIPALES:
- Rental de equipos de producción (cámaras, lighting, audio)
- Alquiler de espacios de producción
- Set support y Unit Manager
- Production Van / Video Van / video village
- División de Blackwall y espacios temporales
- Green Room, backstage y hospitalidad
- Transporte, montaje y desmontaje
- Oficinas de producción
- Vestuario y maquillaje
- Climatización
- Tiendas y mobiliario
- Comunicaciones (walkies, redes, Wi-Fi)
- Electricidad, iluminación y equipos audiovisuales
- Seguridad, limpieza, señalética y consumibles

────────────────────────────────────
RESTRICCIONES DE RESPUESTA
────────────────────────────────────
✅ PUEDES RESPONDER:
- Información general sobre servicios (qué es, para qué sirve)
- Horarios, ubicación, contacto
- Procesos básicos (cómo contactar, pasos generales)

❌ NUNCA PROMETAS:
- Precio, presupuesto o tarifa (incluso estimaciones)
- Disponibilidad específica de fecha/hora/equipo
- Stock o cantidad de equipos
- Fecha/hora de entrega o plazo
- Condiciones contractuales o términos especiales
- Descuentos o promociones
- Reserva o confirmación de servicios
- Recomendación técnica final

────────────────────────────────────
FLUJO DE RECOPILACIÓN DE DATOS
────────────────────────────────────
Si el cliente muestra interés, recopila GRADUALMENTE (máx 1–2 preguntas por mensaje):
1. Nombre
2. Empresa/productora
3. Teléfono o email
4. Tipo de proyecto
5. Servicios solicitados
6. Fechas/duración
7. Localización
8. Personas/equipo estimado
9. Necesidades de transporte/montaje/support
10. Descripción breve del proyecto

────────────────────────────────────
REGLAS ESTRICTAS
────────────────────────────────────
1. NUNCA inventes información sobre precios, disponibilidad o especificaciones
2. NUNCA confirmes una reserva
3. NUNCA hagas promesas sobre fechas de respuesta exactas
4. NUNCA digas que eres una IA o un bot
5. Respuestas cortas y profesionales (máximo 3 frases)
6. El equipo humano confirma; tú solo recopilas datos y haces handoff
"""

# ── WhatsApp webhook ──────────────────────────────────────────────────────────
@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')

        logger.info(f"Message from {sender}: {incoming_msg[:80]}")

        language = detect_language(incoming_msg)
        
        # Check for greeting first
        if is_greeting(incoming_msg):
            reply = GREETING_RESPONSE_EN if language == 'en' else GREETING_RESPONSE_ES
        else:
            # Check for handoff triggers
            handoff = check_handoff(incoming_msg, language)
            if handoff:
                logger.info(f"Handoff triggered for {sender} ({language})")
                save_purocuento_lead("", "", "", "", "", "", "", "", "", incoming_msg)
                reply = handoff
            else:
                # Normal conversation
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

        logger.info(f"Reply to {sender}: {reply[:80]}")

    except Exception as e:
        logger.error(f"whatsapp() error: {e}")
        reply = "Lo sentimos, ha habido un problema. Por favor inténtalo de nuevo. Puedes contactarnos en operativa@purocuento.es o +34 657 654 417."

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

# ── Legacy voice endpoint (disabled) ─────────────────────────────────────────
@app.route('/voice-reservation', methods=['POST'])
def voice_reservation():
    logger.info("voice-reservation endpoint called but disabled for PuroCuento demo")
    return {'status': 'disabled', 'message': 'This legacy demo endpoint is disabled.'}, 410

# ── Legacy IVR endpoint (disabled) ────────────────────────────────────────────
@app.route('/confirm-reservation', methods=['POST'])
def confirm_reservation():
    logger.info("confirm-reservation endpoint called but disabled for PuroCuento demo")
    twiml = '''<?xml version="1.0" encoding="UTF-8"?>
<Response><Say language="es-ES" voice="Polly.Conchita">This legacy demo endpoint is disabled.</Say></Response>'''
    return twiml, 410, {'Content-Type': 'text/xml'}

# ── Legacy data endpoints (retained for service stability, not used by chatbot) ─
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

