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
import sqlite3
import hmac
from functools import wraps
from flask import jsonify, Response

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
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
OWNER_WHATSAPP_NUMBER = os.environ.get('OWNER_WHATSAPP_NUMBER', '').strip()
DB_PATH = os.environ.get('DB_PATH', '/tmp/purocuento_demo.db')

# Conversation states are deliberately explicit so the AI can never answer
# while a teammate owns (or is waiting to own) the conversation.
AI_ACTIVE = 'AI_ACTIVE'
WAITING_FOR_HUMAN = 'WAITING_FOR_HUMAN'
HUMAN_ACTIVE = 'HUMAN_ACTIVE'
CLOSED = 'CLOSED'
VALID_STATES = {AI_ACTIVE, WAITING_FOR_HUMAN, HUMAN_ACTIVE, CLOSED}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS conversations (
            phone TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'AI_ACTIVE',
            language TEXT DEFAULT 'es', updated_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL,
            direction TEXT NOT NULL, sender TEXT NOT NULL, body TEXT NOT NULL,
            created_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS processed_messages (
            sid TEXT PRIMARY KEY, created_at TEXT NOT NULL
          );
        ''')

def now_iso():
    return datetime.now(pytz.timezone('Europe/Madrid')).isoformat(timespec='seconds')

def ensure_conversation(phone, language='es'):
    with db() as conn:
        conn.execute('''INSERT INTO conversations(phone,state,language,updated_at)
          VALUES(?,?,?,?) ON CONFLICT(phone) DO UPDATE SET language=excluded.language,
          updated_at=excluded.updated_at''', (phone, AI_ACTIVE, language, now_iso()))

def get_state(phone):
    with db() as conn:
        row = conn.execute('SELECT state FROM conversations WHERE phone=?', (phone,)).fetchone()
    return row['state'] if row else AI_ACTIVE

def set_state(phone, state):
    if state not in VALID_STATES:
        raise ValueError('Invalid state')
    ensure_conversation(phone)
    with db() as conn:
        conn.execute('UPDATE conversations SET state=?, updated_at=? WHERE phone=?',
                     (state, now_iso(), phone))

def log_message(phone, direction, sender, body):
    ensure_conversation(phone)
    with db() as conn:
        conn.execute('INSERT INTO messages(phone,direction,sender,body,created_at) VALUES(?,?,?,?,?)',
                     (phone, direction, sender, body, now_iso()))

def mark_processed(sid):
    if not sid:
        return True
    try:
        with db() as conn:
            conn.execute('INSERT INTO processed_messages(sid,created_at) VALUES(?,?)', (sid, now_iso()))
        return True
    except sqlite3.IntegrityError:
        return False

def notify_owner(phone, message):
    if not OWNER_WHATSAPP_NUMBER:
        logger.info('Owner notification skipped: OWNER_WHATSAPP_NUMBER not configured')
        return
    target = OWNER_WHATSAPP_NUMBER if OWNER_WHATSAPP_NUMBER.startswith('whatsapp:') else f'whatsapp:{OWNER_WHATSAPP_NUMBER}'
    dashboard_url = request.url_root.rstrip('/') + '/dashboard'
    body = f'🔔 PuroCuento: nuevo contacto para revisión\nCliente: {phone.replace("whatsapp:", "")}\nMensaje: {message[:400]}\nAbrir: {dashboard_url}'
    try:
        sent = twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=target, body=body)
        logger.info('Owner notification accepted by Twilio: sid=%s status=%s to=%s',
                    sent.sid, sent.status, target)
    except Exception as exc:
        logger.error('Owner notification failed: %s', exc)

init_db()

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

def is_qualified_lead(sender, incoming_msg):
    """Escalate when a customer has supplied contact data for a real project.

    A literal handoff keyword is not required. This prevents the assistant from
    promising that the team will contact a completed lead without notifying the
    team or stopping the AI.
    """
    history_text = ' '.join(item.get('content', '') for item in get_history(sender))
    combined = f'{history_text} {incoming_msg}'.lower()
    has_email = bool(re.search(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b', combined))
    has_phone = bool(re.search(r'(?<!\d)(?:\+?34[\s-]?)?[6-9](?:[\s-]?\d){8}(?!\d)', combined))
    project_terms = (
        'necesito', 'alquilar', 'alquiler', 'evento', 'rodaje', 'produccion',
        'producción', 'carpa', 'altavoz', 'equipo', 'espacio', 'transporte',
        'montaje', 'rental', 'event', 'shoot', 'production', 'speaker',
        'equipment', 'space', 'transport', 'green room'
    )
    has_project = any(term in combined for term in project_terms)
    return (has_email or has_phone) and has_project

def is_greeting(text):
    """Return True only when the entire message is a simple greeting.

    Whole-message matching is intentional: substring checks made words such
    as "which", "this", and "shipping" look like the greeting "hi".
    """
    normalized = re.sub(r'[^a-záéíóúüñ\s]', ' ', text.lower())
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    greetings = {
        'hola', 'buenos dias', 'buenos días', 'buenas tardes',
        'buenas noches', 'hello', 'hi', 'hey', 'greetings'
    }
    return normalized in greetings

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

ALTAVOCES Y EQUIPOS DE SONIDO PUBLICADOS:
- JBL Charge 6: 45 W RMS, hasta 24 h de batería, sin micrófono externo. Para camerinos, oficinas, green rooms y unidades pequeñas de unas 15 personas.
- Marshall Acton III: 60 W, necesita corriente, sin micrófono externo. Para green rooms, backstage y espacios premium.
- Tribit StormBox Blast: 90 W, hasta 30 h de batería, sin micrófono externo. Para salas medianas, carpas y grupos de hasta unas 50 personas según acústica y ruido.
- JBL PartyBox Stage 320: 240 W, hasta 18 h de batería y conexión de micrófono. Para eventos, baile, karaoke y megafonía portátil.
- Alto Professional TS115W: 400 W RMS, necesita corriente y admite micrófono. Para PA, voz e instalaciones estables.
- JBL PartyBox 720: 800 W RMS, baterías intercambiables y conexión de micrófono. Para grandes espacios, exteriores y eventos.
Si preguntan qué modelos hay, enuméralos directamente con una guía breve. Después pregunta como máximo dos cosas: tamaño/personas y si hay corriente o necesitan micrófonos. Nunca confirmes precio ni disponibilidad.

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
        message_sid = request.values.get('MessageSid', '')

        if not sender or not incoming_msg or not mark_processed(message_sid):
            return str(MessagingResponse())

        logger.info(f"Message from {sender}: {incoming_msg[:80]}")

        language = detect_language(incoming_msg)
        ensure_conversation(sender, language)
        log_message(sender, 'inbound', 'customer', incoming_msg)

        # Absolute safety gate: no AI output while a human owns, or is about to
        # own, the conversation. The customer message still appears in inbox.
        if get_state(sender) in {WAITING_FOR_HUMAN, HUMAN_ACTIVE}:
            return str(MessagingResponse())
        
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
                set_state(sender, WAITING_FOR_HUMAN)
                notify_owner(sender, incoming_msg)
            elif is_qualified_lead(sender, incoming_msg):
                logger.info(f"Qualified-lead handoff triggered for {sender} ({language})")
                history = get_history(sender)
                summary = ' | '.join(item.get('content', '') for item in history[-8:])
                save_purocuento_lead("", "", sender, "", "", "", "", "", "", summary + ' | ' + incoming_msg)
                reply = ("Perfecto. Ya tengo los datos principales de tu solicitud. "
                         "La he enviado al equipo de PuroCuento para que continúe contigo personalmente."
                         if language == 'es' else
                         "Perfect. I now have the main details of your request. "
                         "I've sent it to the PuroCuento team so they can continue with you personally.")
                set_state(sender, WAITING_FOR_HUMAN)
                notify_owner(sender, incoming_msg)
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

        log_message(sender, 'outbound', 'ai', reply)
        logger.info(f"Reply to {sender}: {reply[:80]}")

    except Exception as e:
        logger.error(f"whatsapp() error: {e}")
        reply = "Lo sentimos, ha habido un problema. Por favor inténtalo de nuevo. Puedes contactarnos en operativa@purocuento.es o +34 657 654 417."
        if 'sender' in locals() and sender:
            set_state(sender, WAITING_FOR_HUMAN)
            log_message(sender, 'outbound', 'system', reply)
            notify_owner(sender, incoming_msg if 'incoming_msg' in locals() else 'Error del asistente')

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

# ── Human takeover dashboard ─────────────────────────────────────────────────
def dashboard_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        username = os.environ.get('DASHBOARD_USERNAME', 'admin')
        password = os.environ.get('DASHBOARD_PASSWORD', '')
        auth = request.authorization
        valid = bool(password and auth and
                     hmac.compare_digest(auth.username or '', username) and
                     hmac.compare_digest(auth.password or '', password))
        if not valid:
            return Response('Authentication required', 401,
                            {'WWW-Authenticate': 'Basic realm="PuroCuento Dashboard"'})
        return fn(*args, **kwargs)
    return wrapped

DASHBOARD_HTML = '''<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PuroCuento · Bandeja WhatsApp</title><style>
:root{--ink:#17202a;--muted:#708090;--line:#e6e8eb;--brand:#e83c54;--bg:#f5f6f8;--green:#13795b}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;color:var(--ink);background:var(--bg)}
header{height:64px;background:#111923;color:#fff;display:flex;align-items:center;padding:0 22px;gap:12px}header b{font-size:19px}.dot{width:10px;height:10px;border-radius:50%;background:#2dd4a7}
main{height:calc(100vh - 64px);display:grid;grid-template-columns:340px 1fr;max-width:1400px;margin:auto;background:#fff}
aside{border-right:1px solid var(--line);overflow:auto}.aside-title{padding:18px;font-weight:750;border-bottom:1px solid var(--line)}
.conv{padding:14px 16px;border-bottom:1px solid var(--line);cursor:pointer}.conv:hover,.conv.active{background:#fff2f4}.phone{font-weight:700}.preview{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:6px;font-size:14px}
.badge{display:inline-block;font-size:11px;font-weight:800;padding:4px 8px;border-radius:99px;margin-top:8px;background:#e9eef4}.badge.WAITING_FOR_HUMAN{background:#fff0c2;color:#785600}.badge.HUMAN_ACTIVE{background:#dff7ed;color:#075c45}.badge.AI_ACTIVE{background:#e7efff;color:#264e9b}
section{display:flex;flex-direction:column;min-width:0}.toolbar{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:9px;flex-wrap:wrap}.toolbar .who{font-weight:800;margin-right:auto}
button{border:0;border-radius:9px;padding:10px 13px;font-weight:750;cursor:pointer}.primary{background:var(--brand);color:#fff}.secondary{background:#e9eef4}.ai{background:#e7efff;color:#264e9b}
#thread{flex:1;overflow:auto;padding:22px;background:#f2eee8}.msg{max-width:72%;padding:10px 13px;border-radius:13px;margin:8px 0;background:#fff;box-shadow:0 1px 1px #0001}.msg.outbound{margin-left:auto;background:#d9fdd3}.sender{font-size:11px;font-weight:800;color:var(--muted);margin-bottom:4px}.time{font-size:10px;color:var(--muted);text-align:right;margin-top:4px}
.composer{display:flex;gap:10px;padding:13px;border-top:1px solid var(--line)}textarea{flex:1;border:1px solid #ccd1d8;border-radius:10px;padding:11px;resize:none;font:inherit}.empty{margin:auto;color:var(--muted);text-align:center;padding:30px}
@media(max-width:700px){header{height:56px}main{height:calc(100vh - 56px);display:block}aside{height:35%;border-right:0;border-bottom:1px solid var(--line)}section{height:65%}.aside-title{padding:10px 14px}.conv{padding:9px 14px}.preview{margin-top:3px}.badge{margin-top:4px}.toolbar{padding:8px}.toolbar button{padding:8px 9px}#thread{padding:10px}.msg{max-width:88%}.composer{padding:8px}}
</style></head><body><header><span class="dot"></span><b>PuroCuento · WhatsApp</b></header>
<main><aside><div class="aside-title">Bandeja de entrada</div><div id="conversations"></div></aside>
<section><div class="toolbar"><span class="who" id="who">Selecciona una conversación</span><button class="primary" onclick="changeState('HUMAN_ACTIVE')">Tomar control</button><button class="ai" onclick="changeState('AI_ACTIVE')">Devolver a IA</button><button class="secondary" onclick="changeState('CLOSED')">Cerrar</button></div>
<div id="thread"><div class="empty">Los mensajes aparecerán aquí en tiempo real.</div></div>
<div class="composer"><textarea id="reply" rows="2" placeholder="Respuesta del equipo…"></textarea><button class="primary" onclick="sendReply()">Enviar</button></div></section></main>
<script>
let selected='';let data=[];const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={AI_ACTIVE:'IA activa',WAITING_FOR_HUMAN:'Esperando humano',HUMAN_ACTIVE:'Humano activo',CLOSED:'Cerrada'};
async function load(){let r=await fetch('/dashboard/api/conversations');if(!r.ok)return;data=await r.json();document.getElementById('conversations').innerHTML=data.map(c=>`<div class="conv ${c.phone===selected?'active':''}" onclick="selectPhone('${esc(c.phone)}')"><div class="phone">${esc(c.phone.replace('whatsapp:',''))}</div><div class="preview">${esc(c.last_message||'Sin mensajes')}</div><span class="badge ${c.state}">${labels[c.state]||c.state}</span></div>`).join('')||'<div class="empty">Aún no hay conversaciones</div>';if(selected)loadThread()}
async function selectPhone(p){selected=p;document.getElementById('who').textContent=p.replace('whatsapp:','');await load();}
async function loadThread(){let r=await fetch('/dashboard/api/thread?phone='+encodeURIComponent(selected));if(!r.ok)return;let x=await r.json();document.getElementById('thread').innerHTML=x.messages.map(m=>`<div class="msg ${m.direction}"><div class="sender">${m.sender==='ai'?'Asistente IA':m.sender==='human'?'Equipo PuroCuento':'Cliente'}</div>${esc(m.body)}<div class="time">${new Date(m.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</div></div>`).join('');let t=document.getElementById('thread');t.scrollTop=t.scrollHeight}
async function changeState(state){if(!selected)return alert('Selecciona una conversación');await fetch('/dashboard/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:selected,state})});load()}
async function sendReply(){let e=document.getElementById('reply'),body=e.value.trim();if(!selected||!body)return;let r=await fetch('/dashboard/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:selected,body})});if(r.ok){e.value='';load()}else alert((await r.json()).error||'No se pudo enviar')}
load();setInterval(load,4000);
</script></body></html>'''

@app.route('/dashboard')
@dashboard_auth
def dashboard():
    return Response(DASHBOARD_HTML, mimetype='text/html')

@app.route('/dashboard/api/conversations')
@dashboard_auth
def dashboard_conversations():
    with db() as conn:
        rows = conn.execute('''SELECT c.*, (SELECT body FROM messages m WHERE m.phone=c.phone ORDER BY id DESC LIMIT 1) last_message
          FROM conversations c ORDER BY c.updated_at DESC''').fetchall()
    return jsonify([dict(row) for row in rows])

@app.route('/dashboard/api/thread')
@dashboard_auth
def dashboard_thread():
    phone = request.args.get('phone', '')
    with db() as conn:
        rows = conn.execute('SELECT * FROM messages WHERE phone=? ORDER BY id', (phone,)).fetchall()
    return jsonify({'phone': phone, 'state': get_state(phone), 'messages': [dict(row) for row in rows]})

@app.route('/dashboard/api/state', methods=['POST'])
@dashboard_auth
def dashboard_state():
    data = request.get_json(silent=True) or {}
    phone, state = data.get('phone', ''), data.get('state', '')
    if not phone or state not in VALID_STATES:
        return jsonify(error='Teléfono o estado no válido'), 400
    set_state(phone, state)
    return jsonify(ok=True, state=state)

@app.route('/dashboard/api/send', methods=['POST'])
@dashboard_auth
def dashboard_send():
    data = request.get_json(silent=True) or {}
    phone, body = data.get('phone', '').strip(), data.get('body', '').strip()
    if not phone or not body:
        return jsonify(error='Falta teléfono o mensaje'), 400
    try:
        twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=phone, body=body)
        set_state(phone, HUMAN_ACTIVE)
        log_message(phone, 'outbound', 'human', body)
        return jsonify(ok=True)
    except Exception as exc:
        logger.error('Human reply failed: %s', exc)
        return jsonify(error='Twilio no pudo enviar el mensaje'), 502

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
