#!/usr/bin/env python3
"""
ServiceTonic Monitor Robot
==========================
Robot de automatización que monitorea ServiceTonic cada N segundos,
detecta nuevos tickets asignados y envía notificaciones por email y Slack.

Uso:
    python3 servicetonic_monitor.py

Configuración:
    Editar config.json antes de ejecutar.
"""

import json
import logging
import os
import smtplib
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ─────────────────────────────────────────────
# CONFIGURACIÓN Y PATHS
# ─────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.json"


def load_config() -> dict:
    """Carga la configuración desde config.json."""
    if not CONFIG_FILE.exists():
        print(f"[ERROR] No se encontró config.json en {SCRIPT_DIR}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()

# Paths de archivos
STATE_FILE = SCRIPT_DIR / CONFIG["files"]["state_file"]
LOG_FILE = SCRIPT_DIR / CONFIG["files"]["log_file"]
SCREENSHOTS_DIR = SCRIPT_DIR / CONFIG["files"]["screenshots_dir"]
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Parámetros del monitor
CHECK_INTERVAL = CONFIG["monitor"]["check_interval_seconds"]
LOGIN_RETRIES = CONFIG["monitor"]["login_retry_attempts"]
PAGE_TIMEOUT = CONFIG["monitor"]["page_load_timeout_ms"]
RETRY_WAIT = CONFIG["monitor"]["retry_wait_seconds"]
HEADLESS = CONFIG["monitor"]["headless"]

# Credenciales ServiceTonic
ST_LOGIN_URL = CONFIG["servicetonic"]["login_url"]
ST_TICKETS_URL = CONFIG["servicetonic"]["tickets_url"]
ST_USER = CONFIG["servicetonic"]["username"]
ST_PASS = CONFIG["servicetonic"]["password"]

# Email
EMAIL_RECIPIENT = CONFIG["email"]["recipient"]
SMTP_HOST = CONFIG["email"]["smtp_host"]
SMTP_PORT = CONFIG["email"]["smtp_port"]
SMTP_USER = CONFIG["email"]["smtp_user"]
SMTP_PASS = CONFIG["email"]["smtp_password"]
USE_TLS = CONFIG["email"]["use_tls"]
SENDER_NAME = CONFIG["email"]["sender_name"]

# Slack
SLACK_WEBHOOK_URL = CONFIG["slack"]["webhook_url"]
SLACK_CHANNEL = CONFIG["slack"]["channel"]
SLACK_USERNAME = CONFIG["slack"]["username"]
SLACK_ICON_EMOJI = CONFIG["slack"]["icon_emoji"]
SLACK_ENABLED = CONFIG["slack"]["enabled"]


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def setup_logging():
    """Configura el sistema de logging con salida a consola y archivo."""
    logger = logging.getLogger("servicetonic")
    logger.setLevel(logging.DEBUG)

    # Formato para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s — %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_fmt)

    # Formato para archivo (más detallado)
    file_handler = logging.FileHandler(
        SCRIPT_DIR / "robot_debug.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


logger = setup_logging()


# ─────────────────────────────────────────────
# LOG CSV (ticket_monitor.log)
# ─────────────────────────────────────────────

def write_csv_log(status: str, ticket_id: str = "N/A"):
    """
    Escribe una línea en el log CSV con formato:
    YYYY-MM-DD,HH:MM:SS,FOUND/NOT_FOUND,{ticket_id}
    El archivo NUNCA se sobreescribe, siempre se hace append.
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    line = f"{date_str},{time_str},{status},{ticket_id}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    logger.debug(f"LOG CSV → {line.strip()}")


# ─────────────────────────────────────────────
# PERSISTENCIA DE ESTADO (last_tickets.json)
# ─────────────────────────────────────────────

def load_last_tickets() -> set:
    """
    Carga el conjunto de IDs de tickets conocidos desde last_tickets.json.
    Si el archivo no existe, retorna un conjunto vacío.
    """
    if not STATE_FILE.exists():
        logger.info("No se encontró last_tickets.json — primera ejecución.")
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = set(data.get("ticket_ids", []))
        logger.debug(f"Estado cargado: {len(ids)} tickets conocidos.")
        return ids
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Error leyendo last_tickets.json: {e}. Iniciando con estado vacío.")
        return set()


def save_last_tickets(ticket_ids: set):
    """
    Persiste el conjunto actual de IDs de tickets en last_tickets.json.
    """
    data = {
        "ticket_ids": sorted(list(ticket_ids)),
        "last_updated": datetime.now().isoformat()
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.debug(f"Estado guardado: {len(ticket_ids)} tickets.")


# ─────────────────────────────────────────────
# NOTIFICACIONES EMAIL
# ─────────────────────────────────────────────

def send_email_notification(ticket_id: str, detection_time: datetime):
    """
    Envía una notificación por email para un nuevo ticket detectado.
    Si el envío falla, registra el error pero NO detiene el loop.
    """
    if not SMTP_USER or not SMTP_PASS:
        logger.warning(
            f"Email NO enviado para ticket {ticket_id}: "
            "smtp_user y smtp_password no configurados en config.json."
        )
        return False

    subject = f"[ServiceTonic] Nuevo ticket asignado - ID: {ticket_id}"
    body = (
        f"Se ha detectado un nuevo ticket asignado en ServiceTonic.\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Fecha/Hora de detección: {detection_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Enlace al sistema: {ST_LOGIN_URL}\n\n"
        f"Este es un mensaje automático. No responder a este correo."
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{SENDER_NAME} <{SMTP_USER}>"
        msg["To"] = EMAIL_RECIPIENT

        # Parte texto plano
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Parte HTML
        html_body = f"""
        <html><body>
        <h2 style="color:#d9534f;">🎫 Nuevo Ticket Asignado en ServiceTonic</h2>
        <table style="border-collapse:collapse;font-family:Arial,sans-serif;">
          <tr><td style="padding:8px;font-weight:bold;">Ticket ID:</td>
              <td style="padding:8px;color:#0275d8;font-size:18px;"><strong>{ticket_id}</strong></td></tr>
          <tr><td style="padding:8px;font-weight:bold;">Fecha/Hora:</td>
              <td style="padding:8px;">{detection_time.strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
          <tr><td style="padding:8px;font-weight:bold;">Sistema:</td>
              <td style="padding:8px;"><a href="{ST_LOGIN_URL}">{ST_LOGIN_URL}</a></td></tr>
        </table>
        <p style="color:#888;font-size:12px;margin-top:20px;">
          Este es un mensaje automático. No responder a este correo.
        </p>
        </body></html>
        """
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            if USE_TLS:
                server.starttls()
                server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, EMAIL_RECIPIENT, msg.as_string())

        logger.info(f"Email enviado a {EMAIL_RECIPIENT} para ticket {ticket_id}.")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"Error de autenticación SMTP para ticket {ticket_id}: {e}")
    except smtplib.SMTPException as e:
        logger.error(f"Error SMTP al enviar email para ticket {ticket_id}: {e}")
    except Exception as e:
        logger.error(f"Error inesperado al enviar email para ticket {ticket_id}: {e}")
    return False


# ─────────────────────────────────────────────
# NOTIFICACIONES SLACK
# ─────────────────────────────────────────────

def send_slack_notification(ticket_id: str, detection_time: datetime) -> bool:
    """
    Envía una notificación a un canal de Slack via Incoming Webhook.
    Usa solo librerías estándar de Python (urllib), sin dependencias externas.
    Si el envío falla, registra el error pero NO detiene el loop.
    """
    if not SLACK_ENABLED:
        logger.debug("Notificación Slack deshabilitada (enabled: false en config.json).")
        return False

    if not SLACK_WEBHOOK_URL:
        logger.warning(
            f"Slack NO notificado para ticket {ticket_id}: "
            "slack.webhook_url no configurado en config.json."
        )
        return False

    fecha_hora = detection_time.strftime("%Y-%m-%d %H:%M:%S")

    # Payload con Block Kit de Slack para un mensaje visualmente rico
    payload = {
        "channel": SLACK_CHANNEL,
        "username": SLACK_USERNAME,
        "icon_emoji": SLACK_ICON_EMOJI,
        "text": f":ticket: *Nuevo ticket asignado en ServiceTonic — ID: {ticket_id}*",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":ticket: Nuevo Ticket Asignado — ID: {ticket_id}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Ticket ID:*\n`{ticket_id}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Detectado el:*\n{fecha_hora}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Sistema:* <{ST_LOGIN_URL}|Abrir ServiceTonic>"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Mensaje automático generado por ServiceTonic Monitor Robot_"
                    }
                ]
            }
        ]
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            resp_body = response.read().decode("utf-8")
            if response.status == 200 and resp_body == "ok":
                logger.info(
                    f"Slack notificado correctamente para ticket {ticket_id} "
                    f"(canal: {SLACK_CHANNEL})."
                )
                return True
            else:
                logger.error(
                    f"Slack respondió con status {response.status}: {resp_body} "
                    f"para ticket {ticket_id}."
                )
                return False

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        logger.error(
            f"Error HTTP al enviar Slack para ticket {ticket_id}: "
            f"{e.code} {e.reason} — {body}"
        )
    except urllib.error.URLError as e:
        logger.error(
            f"Error de conexión al enviar Slack para ticket {ticket_id}: {e.reason}"
        )
    except Exception as e:
        logger.error(
            f"Error inesperado al enviar Slack para ticket {ticket_id}: {e}"
        )
    return False


# ─────────────────────────────────────────────
# BROWSER AUTOMATION (Playwright)
# ─────────────────────────────────────────────

class ServiceTonicMonitor:
    """
    Robot principal que gestiona la sesión de ServiceTonic
    y extrae los tickets asignados.
    """

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.is_logged_in = False

    def start_browser(self):
        """Inicia el navegador Playwright en modo headless."""
        logger.info("Iniciando navegador...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(PAGE_TIMEOUT)
        logger.info("Navegador iniciado correctamente.")

    def stop_browser(self):
        """Cierra el navegador y libera recursos."""
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            logger.warning(f"Error al cerrar navegador: {e}")
        self.is_logged_in = False
        logger.info("Navegador cerrado.")

    def take_screenshot(self, name: str) -> str:
        """Toma un screenshot para debugging."""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOTS_DIR / f"{name}_{ts}.png"
            self.page.screenshot(path=str(path))
            logger.debug(f"Screenshot guardado: {path}")
            return str(path)
        except Exception as e:
            logger.warning(f"No se pudo tomar screenshot '{name}': {e}")
            return ""

    def is_on_login_page(self) -> bool:
        """Verifica si la página actual es el login (sesión expirada)."""
        try:
            current_url = self.page.url
            return "login.jsf" in current_url
        except Exception:
            return True

    def login(self) -> bool:
        """
        Realiza el proceso de login en ServiceTonic.
        Reintenta hasta LOGIN_RETRIES veces.
        Retorna True si el login fue exitoso, False en caso contrario.
        """
        for attempt in range(1, LOGIN_RETRIES + 1):
            logger.info(f"Intento de login {attempt}/{LOGIN_RETRIES}...")
            try:
                self.page.goto(ST_LOGIN_URL, wait_until="networkidle")

                # Verificar si ya está logueado (redirigió automáticamente)
                if not self.is_on_login_page():
                    logger.info("Sesión activa detectada — omitiendo login.")
                    self.is_logged_in = True
                    return True

                # Llenar formulario de login
                username_field = self.page.locator("#frmLogin\\:j_username")
                password_field = self.page.locator("#frmLogin\\:j_password")
                login_btn = self.page.locator("#frmLogin\\:loginBtn")

                username_field.wait_for(state="visible", timeout=10000)
                username_field.fill(ST_USER)
                password_field.fill(ST_PASS)
                login_btn.click()

                # Esperar a que la página cargue después del login
                try:
                    self.page.wait_for_url(
                        lambda url: "login.jsf" not in url,
                        timeout=20000
                    )
                except PlaywrightTimeoutError:
                    # Puede que ya esté en la página correcta
                    pass
                self.page.wait_for_load_state("domcontentloaded", timeout=15000)

                # Verificar login exitoso
                if not self.is_on_login_page():
                    logger.info("Login exitoso.")
                    self.is_logged_in = True
                    return True
                else:
                    logger.warning(f"Login fallido en intento {attempt} — sigue en login page.")
                    self.take_screenshot(f"login_failed_attempt_{attempt}")

            except PlaywrightTimeoutError as e:
                logger.error(f"Timeout durante login (intento {attempt}): {e}")
                self.take_screenshot(f"login_timeout_attempt_{attempt}")
            except Exception as e:
                logger.error(f"Error inesperado durante login (intento {attempt}): {e}")
                self.take_screenshot(f"login_error_attempt_{attempt}")

            if attempt < LOGIN_RETRIES:
                logger.info(f"Esperando {RETRY_WAIT}s antes de reintentar login...")
                time.sleep(RETRY_WAIT)

        logger.critical(
            f"Login fallido después de {LOGIN_RETRIES} intentos. "
            "Deteniendo robot."
        )
        write_csv_log("ERROR_LOGIN", "N/A")
        self.is_logged_in = False
        return False

    def ensure_logged_in(self) -> bool:
        """
        Verifica si la sesión está activa. Si no, intenta re-login.
        Retorna True si la sesión es válida, False si el login falla.
        """
        if self.is_on_login_page():
            logger.warning("Sesión expirada detectada — intentando re-login...")
            self.is_logged_in = False
            return self.login()
        return True

    def get_assigned_tickets(self) -> list[dict]:
        """
        Navega a la vista de tickets y extrae todos los tickets visibles.
        Retorna una lista de dicts con id, titulo, estado, asignado_a, fecha_creacion.
        Lanza excepción si hay error de página.
        """
        logger.debug(f"Navegando a vista de tickets: {ST_TICKETS_URL}")

        try:
            self.page.goto(ST_TICKETS_URL, wait_until="networkidle")
        except PlaywrightTimeoutError:
            logger.warning("Timeout al cargar página de tickets — reintentando...")
            time.sleep(RETRY_WAIT)
            self.page.goto(ST_TICKETS_URL, wait_until="domcontentloaded")

        # Verificar si fue redirigido al login
        if self.is_on_login_page():
            raise SessionExpiredError("Redirigido al login al cargar tickets.")

        # Esperar a que la tabla de tickets esté presente
        try:
            self.page.wait_for_selector(
                "#frmDT\\:dtItems_data",
                state="visible",
                timeout=20000
            )
        except PlaywrightTimeoutError:
            self.take_screenshot("tickets_table_missing")
            raise PageStructureError(
                "Tabla de tickets no encontrada. Screenshot guardado."
            )

        # Extraer todos los tickets de la tabla
        tickets = self.page.evaluate("""
        () => {
            const results = [];
            const tbody = document.getElementById('frmDT:dtItems_data');
            if (!tbody) return results;

            const rows = tbody.querySelectorAll('tr');
            rows.forEach((row, rowIndex) => {
                try {
                    // ID del ticket: enlace con id que termina en ":hool"
                    const idLinks = row.querySelectorAll('a[id$=":hool"]');
                    // El primer enlace ":hool" sin subcampo es el ID del ticket
                    let ticketId = null;
                    let titleText = null;
                    let ticketType = null;
                    let creationDate = null;
                    let status = null;
                    let assignedTo = null;

                    idLinks.forEach(link => {
                        const linkId = link.id;
                        // ID principal: frmDT:dtItems:N:hool (sin subcampo adicional)
                        if (/frmDT:dtItems:\\d+:hool$/.test(linkId)) {
                            ticketId = link.textContent.trim();
                        }
                        // Título: frmDT:dtItems:N:j_idt276:0:hool
                        if (/:j_idt276:0:hool$/.test(linkId)) {
                            titleText = link.textContent.trim();
                        }
                        // Tipo ticket: :j_idt276:1:hool
                        if (/:j_idt276:1:hool$/.test(linkId)) {
                            ticketType = link.textContent.trim();
                        }
                        // Fecha creación: :j_idt276:2:hool
                        if (/:j_idt276:2:hool$/.test(linkId)) {
                            creationDate = link.textContent.trim();
                        }
                    });

                    // Estado y asignado_a pueden estar en celdas de texto
                    const cells = row.querySelectorAll('td');
                    cells.forEach(cell => {
                        const text = cell.textContent.trim();
                        if (text.startsWith('Estado')) {
                            status = text.replace('Estado', '').trim();
                        }
                        if (text.startsWith('Asignado a')) {
                            assignedTo = text.replace('Asignado a', '').trim();
                        }
                    });

                    if (ticketId) {
                        results.push({
                            id: ticketId,
                            title: titleText || '',
                            type: ticketType || '',
                            creation_date: creationDate || '',
                            status: status || '',
                            assigned_to: assignedTo || ''
                        });
                    }
                } catch(e) {
                    // Ignorar filas con error
                }
            });
            return results;
        }
        """)

        logger.debug(f"Tickets extraídos de la página: {len(tickets)}")
        return tickets

    def get_all_ticket_ids(self) -> set:
        """
        Extrae todos los IDs de tickets de la vista actual,
        incluyendo paginación si existe.
        Retorna un set de strings con los IDs.
        """
        all_ids = set()
        page_num = 1

        while True:
            logger.debug(f"Extrayendo tickets — página {page_num}...")
            tickets = self.get_assigned_tickets()

            if not tickets:
                logger.debug("No se encontraron tickets en esta página.")
                break

            page_ids = {t["id"] for t in tickets}
            all_ids.update(page_ids)
            logger.debug(f"Página {page_num}: {len(page_ids)} tickets encontrados.")

            # Verificar si hay botón "Siguiente" para paginación
            try:
                next_btn = self.page.locator(
                    "a.ui-paginator-next:not(.ui-state-disabled)"
                )
                if next_btn.count() > 0:
                    next_btn.click()
                    self.page.wait_for_load_state("networkidle")
                    page_num += 1
                else:
                    break
            except Exception:
                break

        return all_ids


# ─────────────────────────────────────────────
# EXCEPCIONES PERSONALIZADAS
# ─────────────────────────────────────────────

class SessionExpiredError(Exception):
    """Se lanza cuando la sesión de ServiceTonic ha expirado."""
    pass


class PageStructureError(Exception):
    """Se lanza cuando la estructura esperada de la página no se encuentra."""
    pass


# ─────────────────────────────────────────────
# LOOP PRINCIPAL
# ─────────────────────────────────────────────

def run_monitor():
    """
    Loop principal del robot de monitoreo.
    Ejecuta indefinidamente hasta ser detenido manualmente (Ctrl+C).
    """
    logger.info("=" * 60)
    logger.info("  ServiceTonic Monitor — Iniciando robot...")
    logger.info(f"  Intervalo de verificación: {CHECK_INTERVAL}s")
    logger.info(f"  Email de notificación: {EMAIL_RECIPIENT}")
    logger.info(f"  Log CSV: {LOG_FILE}")
    logger.info("=" * 60)

    monitor = ServiceTonicMonitor()

    try:
        monitor.start_browser()
    except Exception as e:
        logger.critical(f"No se pudo iniciar el navegador: {e}")
        sys.exit(1)

    # Login inicial
    if not monitor.login():
        monitor.stop_browser()
        logger.critical("Login inicial fallido. Abortando.")
        sys.exit(1)

    # Cargar estado previo de tickets
    known_tickets = load_last_tickets()
    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            cycle_start = datetime.now()
            logger.info(f"─── Ciclo #{cycle_count} — {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} ───")

            try:
                # Verificar sesión activa
                if not monitor.ensure_logged_in():
                    logger.critical("No se pudo restaurar la sesión. Deteniendo robot.")
                    write_csv_log("ERROR_SESSION", "N/A")
                    break

                # Obtener tickets actuales
                current_tickets = monitor.get_all_ticket_ids()
                logger.info(
                    f"Tickets visibles: {len(current_tickets)} — "
                    f"IDs: {sorted(current_tickets)[:10]}{'...' if len(current_tickets) > 10 else ''}"
                )

                # Detectar tickets nuevos
                if known_tickets:
                    new_tickets = current_tickets - known_tickets
                else:
                    # Primera ejecución: no enviar alertas, solo registrar estado
                    new_tickets = set()
                    logger.info(
                        "Primera ejecución: registrando estado inicial sin enviar alertas."
                    )

                if new_tickets:
                    logger.info(f"NUEVOS TICKETS DETECTADOS: {sorted(new_tickets)}")
                    for ticket_id in sorted(new_tickets):
                        detection_time = datetime.now()
                        # Enviar email
                        send_email_notification(ticket_id, detection_time)
                        # Enviar notificación Slack
                        send_slack_notification(ticket_id, detection_time)
                        # Log CSV
                        write_csv_log("FOUND", ticket_id)
                        logger.info(
                            f"LOG → {detection_time.strftime('%Y-%m-%d')},{detection_time.strftime('%H:%M:%S')},FOUND,{ticket_id}"
                        )
                else:
                    write_csv_log("NOT_FOUND", "N/A")
                    logger.info(
                        f"LOG → {cycle_start.strftime('%Y-%m-%d')},{cycle_start.strftime('%H:%M:%S')},NOT_FOUND,N/A"
                    )

                # Actualizar estado persistido
                known_tickets = current_tickets
                save_last_tickets(known_tickets)

            except SessionExpiredError as e:
                logger.warning(f"Sesión expirada: {e}")
                write_csv_log("SESSION_EXPIRED", "N/A")
                monitor.is_logged_in = False
                # El siguiente ciclo intentará re-login

            except PageStructureError as e:
                logger.error(f"Error de estructura de página: {e}")
                write_csv_log("PAGE_ERROR", "N/A")
                # Continúa el loop

            except PlaywrightTimeoutError as e:
                logger.error(f"Timeout de página: {e}")
                write_csv_log("TIMEOUT_ERROR", "N/A")
                monitor.take_screenshot("timeout_error")
                logger.info(f"Esperando {RETRY_WAIT}s tras timeout...")
                time.sleep(RETRY_WAIT)

            except Exception as e:
                logger.error(f"Error inesperado en ciclo #{cycle_count}: {e}", exc_info=True)
                write_csv_log("UNEXPECTED_ERROR", "N/A")
                monitor.take_screenshot(f"unexpected_error_cycle_{cycle_count}")

                # Si el navegador crasheó, reiniciarlo
                try:
                    monitor.page.url  # Verificar si el browser sigue vivo
                except Exception:
                    logger.warning("Navegador inaccesible — reiniciando...")
                    monitor.stop_browser()
                    time.sleep(RETRY_WAIT)
                    try:
                        monitor.start_browser()
                        monitor.login()
                    except Exception as restart_err:
                        logger.critical(f"No se pudo reiniciar el navegador: {restart_err}")
                        break

            # Calcular tiempo de espera real
            elapsed = (datetime.now() - cycle_start).total_seconds()
            wait_time = max(0, CHECK_INTERVAL - elapsed)
            logger.info(
                f"Ciclo #{cycle_count} completado en {elapsed:.1f}s. "
                f"Próxima verificación en {wait_time:.0f}s..."
            )
            time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info("\nRobot detenido manualmente por el usuario (Ctrl+C).")
    finally:
        monitor.stop_browser()
        logger.info("Robot finalizado.")


# ─────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_monitor()
