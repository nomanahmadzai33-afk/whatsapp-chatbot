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
SHEET_NAME = "Du Liban Reservations"   # Keeps original for reference; PuroCuento leads go to separate worksheet

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

def save_purocuento_lead(name, company, phone_or_email, project_type, services, dates, location, people, logistics, description):
    """Save PuroCuento demo enquiry to 'PuroCuento Leads' worksheet. Creates it if missing."""
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        try:
            sheet = gc.open(SHEET_NAME).worksheet("PuroCuento Leads")
        except Exception:
            spreadsheet = gc.open(SHEET_NAME)
            sheet = spreadsheet.add_worksheet(title="PuroCuento Leads", rows=1000, cols=10)
            sheet.append_row([
                "Timestamp", "Name", "Company", "Phone/Email", "Project Type",
                "Services", "Dates", "Location", "People", "Logistics/Support", "Description", "Status"
            ])
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([
            now, name, company, phone_or_email, project_type,
            services, dates, location, people, logistics, description, "PENDING"
        ])
        logger.info(f"PuroCuento lead saved: {name} from {company}")
        return True
    except Exception as e:
        logger.error(f"save_purocu ento_lead error: {e}")
        return False

def log_escalation(sender, reason):
    """Log escalation/handoff without persisting to Sheets in this demo version."""
    logger.info(f"ESCALATION from {sender}: {reason}")

# ── Handoff detection (Spanish and English) ──────────────────────────────────
HANDOFF_KEYWORDS = {
    'es': [
        r'precio', r'presupuesto', r'costo', r'tarifa',
        r'disponibilidad', r'disponible', r'stock',
        r'reserva', r'reservar', r'booking',
        r'urgent', r'urgencia', r'asap',
        r'queja', r'problema', r'reclamación',
        r'contrato', r'condiciones', r'términos',
        r'oferta', r'descuento', r'promoción',
        r'entrega', r'transporte', r'envío',
        r'negociación', r'hablo con', r'habla con', r'quiero hablar'
    ],
    'en': [
        r'price', r'quote', r'cost', r'rate',
        r'availability', r'available', r'stock',
        r'reservation', r'reserve', r'booking',
        r'urgent', r'urgency', r'asap',
        r'complaint', r'issue', r'problem',
        r'contract', r'terms', r'conditions',
        r'offer', r'discount', r'promotion',
        r'delivery', r'transport', r'shipping',
        r'negotiate', r'speak', r'talk', r'i want to speak'
    ]
}

def detect_handoff(text, language):
    """Detect if user message requires human handoff based on keywords."""
    if language not in HANDOFF_KEYWORDS:
        language = 'es'
    text_lower = text.lower()
    for pattern in HANDOFF_KEYWORDS[language]:
        if re.search(pattern, text_lower):
            return True
    return False

# ── Time helpers ──────────────────────────────────────────────────────────────
def get_madrid_time():
    madrid_tz = pytz.timezone('Europe/Madrid')
    return datetime.now(madrid_tz)

def get_business_hours_status(now):
    """
    Returns business hours status for PuroCuento (Madrid timezone).
    Hours: Monday–Friday 09:00–14:00 and 16:00–18:00
    """
    weekday = now.weekday()  # 0=Monday … 6=Sunday
    hour = now.hour
    minute = now.minute
    time_mins = hour * 60 + minute

    # Outside Mon–Fri
    if weekday > 4:  # Saturday (5) or Sunday (6)
        return "FUERA_HORARIO_WEEKEND"

    # Outside business hours
    if time_mins < 9 * 60:  # before 09:00
        return "FUERA_HORARIO_MANANA"
    if 14 * 60 <= time_mins < 16 * 60:  # 14:00–16:00 (lunch break)
        return "DESCANSO_MEDIODIA"
    if time_mins >= 18 * 60:  # after 18:00
        return "FUERA_HORARIO_TARDE"

    # Within business hours
    return "HORARIO_ABIERTO"

# ── System prompt ─────────────────────────────────────────────────────────────
def get_system_prompt():
    now = get_madrid_time()
    current_date = now.strftime("%A, %d de %B de %Y")
    current_time = now.strftime("%H:%M")
    hours_status = get_business_hours_status(now)

    # Build hours notice block
    if hours_status == "FUERA_HORARIO_WEEKEND":
        hours_notice = """⚠️ ESTADO ACTUAL: Fuera de horario (fin de semana).
PuroCuento abre de lunes a viernes: 09:00–14:00 y 16:00–18:00 (hora de Madrid).
Tu solicitud se recibirá ahora pero el equipo responderá el próximo día laborable."""

    elif hours_status == "FUERA_HORARIO_MANANA":
        hours_notice = """⚠️ ESTADO ACTUAL: Aún no hemos abierto.
Horario: lunes a viernes, 09:00–14:00 y 16:00–18:00 (hora de Madrid)."""

    elif hours_status == "DESCANSO_MEDIODIA":
        hours_notice = """⚠️ ESTADO ACTUAL: Descanso de mediodía (14:00–16:00).
Volvemos a atender en unos momentos. Tu solicitud se procesará cuando el equipo regrese."""

    elif hours_status == "FUERA_HORARIO_TARDE":
        hours_notice = """⚠️ ESTADO ACTUAL: Fuera de horario.
Horario: lunes a viernes, 09:00–14:00 y 16:00–18:00 (hora de Madrid).
Tu solicitud se recibirá ahora pero el equipo responderá mañana."""

    else:  # HORARIO_ABIERTO
        hours_notice = "✅ ESTADO ACTUAL: Horario abierto. El equipo está disponible."

    return f"""Eres el asistente virtual de WhatsApp de PuroCuento, empresa madrileña especializada en servicios de producción audiovisual y de eventos. Eres PuroCuento's AI assistant. Eres profesional, conciso y completamente humano. Nunca suenes como robot o IA. Nunca digas que eres IA.

FECHA Y HORA ACTUAL (Madrid): {current_date}, {current_time}

{hours_notice}

────────────────────────────────────
IDIOMA
────────────────────────────────────
Detecta el idioma del cliente al instante y responde en ese mismo idioma durante toda la conversación.
- Español (por defecto) → español natural de España, profesional
- Inglés → inglés nativo, profesional
- Si no reconoces el idioma, responde en español

TONO: Profesional, conciso, accesible. Máximo 2–3 frases por respuesta. Nunca robótico. Sin emojis excesivos.

────────────────────────────────────
INFORMACIÓN DE PUROCU ENTO
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
- Precio, presupuesto o tarifa (incluso estimaciones vagas)
- Disponibilidad específica de fecha/hora/equipo
- Stock o cantidad de equipos
- Fecha/hora de entrega o plazo
- Condiciones contractuales o términos especiales
- Descuentos o promociones
- Reserva o confirmación de servicios
- Recomendación técnica final

SI EL CLIENTE PREGUNTA POR CUALQUIERA DE LO ANTERIOR → HANDOFF AUTOMÁTICO:
"Gracias por tu interés. Para precio, disponibilidad, presupuesto o reserva, necesito pasar tu solicitud al equipo de PuroCuento. Voy a recopilar los datos básicos de tu proyecto y el equipo te responderá con una propuesta personalizada."

────────────────────────────────────
FLUJO DE RECOPILACIÓN DE DATOS
────────────────────────────────────
Si el cliente muestra interés en servicios, recopila GRADUALMENTE (máx 1–2 preguntas por mensaje):
1. Nombre (si no se ha dado)
2. Empresa/empresa productora
3. Teléfono o email
4. Tipo de proyecto (rodaje, evento, post-producción, etc.)
5. Servicios solicitados (qué necesita rentales/espacios/support)
6. Fechas/duración (cuándo necesita los servicios)
7. Localización (dónde se realiza el proyecto)
8. Personas/equipo estimado
9. Necesidades de transporte/montaje/support
10. Descripción breve del proyecto

RESPUESTA DE HANDOFF (FINAL):
"Gracias, [nombre]. Voy a pasar tu solicitud al equipo de PuroCuento para que revise disponibilidad y prepare una respuesta. No confirmaré precio ni disponibilidad automáticamente; el equipo se pondrá en contacto contigo a través de [teléfono/email] dentro de [24 horas/próximo día hábil]."

LUEGO: Ejecuta SAVE_PUROCU ENTO_LEAD y log_escalation()

────────────────────────────────────
PREGUNTAS FRECUENTES (responde solo con info general)
────────────────────────────────────
- ¿Qué servicios ofertan? → "Ofrecemos rental de equipos de producción, espacios, set support, transporte, etc. Para saber exactamente qué necesitas, cuéntame sobre tu proyecto."
- ¿Dónde están? → "Basados en Madrid, pero servimos proyectos en toda España."
- ¿Horarios? → "Lunes a viernes, 09:00–14:00 y 16:00–18:00 (hora de Madrid)."
- ¿Cómo contacto? → "+34 657 654 417 (WhatsApp disponible) o operativa@purocuento.es"

────────────────────────────────────
REGLAS ESTRICTAS
────────────────────────────────────
1. NUNCA inventes información sobre precios, disponibilidad o especificaciones técnicas
2. NUNCA confirmes una reserva o compromiso de servicio
3. NUNCA hagas promesas sobre fechas de respuesta exactas
4. NUNCA digas que eres una IA o un bot
5. Si detectas palabras clave de precio/disponibilidad/contrato → HANDOFF INMEDIATO
6. Respuestas cortas y profesionales siempre (máximo 3 frases)
7. El equipo humano confirma y cierra; tú solo recopilas datos y haces handoff
8. Tu rol: asistente de recopilación de datos, no vendedor ni asesor técnico final
"""

# ── Conversation history (file-based persistence) ────────────────────────────
MAX_HISTORY = 50
CONV_DIR = "/tmp/conversations"
os.makedirs(CONV_DIR, exist_ok=True)

def _conv_path(sender: str) -> str:
    """Return a safe file path for a sender's conversation history."""
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
    """Persist conversation history to disk, trimmed to MAX_HISTORY messages."""
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    path = _conv_path(sender)
    with open(path, "w") as f:
        json.dump(history, f)

# ── Detect language from message ───────────────────────────────────────────────
def detect_language(text):
    """Simple language detection. Returns 'en' or 'es' (default)."""
    text_lower = text.lower()
    en_indicators = ['hello', 'hi', 'what', 'how', 'can', 'need', 'is', 'the', 'do', 'you']
    en_count = sum(1 for word in en_indicators if word in text_lower)
    if en_count >= 2:
        return 'en'
    return 'es'

# ── WhatsApp webhook ──────────────────────────────────────────────────────────
@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender       = request.values.get('From', '')

        logger.info(f"Message from {sender}: {incoming_msg[:80]}")

        language = detect_language(incoming_msg)
        
        # Check for handoff triggers
        if detect_handoff(incoming_msg, language):
            logger.info(f"Handoff trigger detected for {sender}")
            log_escalation(sender, incoming_msg)
            if language == 'en':
                reply = "Thank you for your interest. For pricing, availability, or quotation, I need to pass your request to the PuroCuento team. They will prepare a personalized proposal and contact you soon. What is your name and company?"
            else:
                reply = "Gracias por tu interés. Para precio, disponibilidad o presupuesto, necesito pasar tu solicitud al equipo de PuroCuento. Preparará una propuesta personalizada y se pondrá en contacto pronto. ¿Cuál es tu nombre y empresa?"
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

        logger.info(f"Reply to {sender}: {reply[:80]}")

    except Exception as e:
        logger.error(f"whatsapp() error: {e}")
        reply = 'Lo sentimos, ha habido un problema. Por favor inténtalo de nuevo o contáctanos al +34 657 654 417.'

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

# ── Voice receptionist endpoint (DISABLED for PuroCuento demo) ────────────────
@app.route('/voice-reservation', methods=['POST'])
def voice_reservation():
    """Disabled for PuroCuento demo. Returns 405 Not Allowed."""
    return {'status': 'disabled', 'message': 'Voice endpoint not available for PuroCuento demo'}, 405

# ── IVR confirmation (DISABLED for PuroCuento demo) ──────────────────────────
@app.route('/confirm-reservation', methods=['POST'])
def confirm_reservation():
    """Disabled for PuroCuento demo. Returns 405 Not Allowed."""
    return {'status': 'disabled', 'message': 'Confirmation endpoint not available for PuroCuento demo'}, 405

# ── Legacy data read endpoints (retained for backward compatibility, not invoked by chatbot) ─
@app.route('/get-reservations', methods=['GET'])
def get_reservations():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).sheet1
        records = sheet.get_all_records()
        return {'reservations': records}, 200
    except Exception as e:
        logger.error(f"get_reservations error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-orders', methods=['GET'])
def get_orders():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Pedidos")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'orders': records}, 200
    except Exception as e:
        logger.error(f"get_orders error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-customers', methods=['GET'])
def get_customers():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Customers")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'customers': records}, 200
    except Exception as e:
        logger.error(f"get_customers error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-tables', methods=['GET'])
def get_tables():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Tables")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'tables': records}, 200
    except Exception as e:
        logger.error(f"get_tables error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-pending-actions', methods=['GET'])
def get_pending_actions():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("PendingActions")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'actions': records}, 200
    except Exception as e:
        logger.error(f"get_pending_actions error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-table', methods=['POST'])
def update_table():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        data     = request.get_json()
        table_id = data.get('table_id')
        status   = data.get('status')
        guest    = data.get('guest_name', '')
        gc       = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Tables")
            records = sheet.get_all_records()
            for i, r in enumerate(records):
                if str(r.get('TableID', '')) == str(table_id):
                    sheet.update_cell(i + 2, 2, status)
                    sheet.update_cell(i + 2, 3, guest)
                    return {'status': 'success'}, 200
            sheet.append_row([table_id, status, guest])
        except Exception:
            pass
        return {'status': 'ok', 'note': 'Tables sheet not found; skipped'}, 200
    except Exception as e:
        logger.error(f"update_table error: {e}")
        return {'error': str(e)}, 500

@app.route('/resolve-pending-action', methods=['POST'])
def resolve_pending_action():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        data    = request.get_json()
        row_num = data.get('row')
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).worksheet("PendingActions")
        headers = sheet.row_values(1)
        status_col = headers.index('Estado') + 1
        sheet.update_cell(row_num, status_col, 'HECHO')
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"resolve_pending_action error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-reservation', methods=['POST'])
def update_reservation():
    """Legacy endpoint. Not used by PuroCuento chatbot."""
    try:
        data    = request.get_json()
        row_num = data.get('row')
        field   = data.get('field')
        value   = data.get('value')
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).sheet1
        headers = sheet.row_values(1)
        col     = headers.index(field) + 1
        sheet.update_cell(row_num, col, value)
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"update_reservation error: {e}")
        return {'error': str(e)}, 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return {'status': 'ok', 'service': 'PuroCuento WhatsApp AI Demo'}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)

