#!/usr/bin/env python3
"""
daily_summary.py — Resumen diario de tickets detectados por ServiceTonic Monitor.

Lee el archivo ticket_monitor.log, filtra las entradas del día actual,
y envía un mensaje consolidado al canal de Slack configurado.

Uso:
    python3 daily_summary.py
    python3 daily_summary.py --date 2026-03-20   # para una fecha específica
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime, date
from pathlib import Path

# ── Configuración ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
CONFIG_FILE  = SCRIPT_DIR / "config.json"
LOG_FILE     = SCRIPT_DIR / "ticket_monitor.log"

# Las credenciales se inyectan desde variables de entorno (GitHub Actions Secrets)
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")

# Si no viene por env var, intentar leer desde config.json
if not SLACK_WEBHOOK and CONFIG_FILE.exists():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    SLACK_WEBHOOK = cfg.get("slack", {}).get("webhook_url", "")


# ── Lectura del log ────────────────────────────────────────────────────────────

def parse_log(target_date: str) -> dict:
    """
    Lee ticket_monitor.log y extrae las entradas del día indicado.

    Formato del log: YYYY-MM-DD,HH:MM:SS,FOUND/NOT_FOUND/SESSION_EXPIRED,{ticket_id}

    Retorna un dict con:
        - new_tickets: lista de IDs únicos detectados como FOUND
        - total_checks: número total de ciclos ejecutados ese día
        - session_errors: número de errores de sesión
        - first_check: hora del primer ciclo
        - last_check: hora del último ciclo
    """
    result = {
        "new_tickets": [],
        "total_checks": 0,
        "session_errors": 0,
        "first_check": None,
        "last_check": None,
    }

    seen_tickets = set()

    if not LOG_FILE.exists():
        return result

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 4:
                continue

            log_date, log_time, status, ticket_id = parts[0], parts[1], parts[2], parts[3]

            if log_date != target_date:
                continue

            result["total_checks"] += 1

            if result["first_check"] is None:
                result["first_check"] = log_time
            result["last_check"] = log_time

            if status == "FOUND" and ticket_id not in seen_tickets:
                seen_tickets.add(ticket_id)
                result["new_tickets"].append({"id": ticket_id, "time": log_time})

            elif status == "SESSION_EXPIRED":
                result["session_errors"] += 1

    return result


# ── Construcción del mensaje Slack ─────────────────────────────────────────────

def build_slack_message(summary: dict, target_date: str) -> dict:
    """Construye el payload Block Kit para el mensaje de resumen diario."""

    new_tickets   = summary["new_tickets"]
    total_checks  = summary["total_checks"]
    session_errors = summary["session_errors"]
    first_check   = summary.get("first_check") or "—"
    last_check    = summary.get("last_check")  or "—"
    count         = len(new_tickets)

    # Formatear fecha legible
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        fecha_legible = dt.strftime("%A %d de %B de %Y").capitalize()
    except ValueError:
        fecha_legible = target_date

    # Encabezado según resultado
    if count == 0:
        header_text  = "📋 Resumen Diario — Sin tickets nuevos"
        result_emoji = "✅"
        result_text  = "*No hubo nuevos tickets* asignados durante el día."
        color_bar    = "✅"
    else:
        header_text  = f"📋 Resumen Diario — {count} ticket{'s' if count > 1 else ''} nuevo{'s' if count > 1 else ''}"
        result_emoji = "🎫"
        result_text  = f"Se detectaron *{count} ticket{'s' if count > 1 else ''} nuevo{'s' if count > 1 else ''}* asignados durante el día."
        color_bar    = "🔔"

    blocks = [
        # Encabezado
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": True}
        },
        # Fecha
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Fecha:* {fecha_legible}"
            }
        },
        {"type": "divider"},
        # Resultado principal
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{result_emoji}  {result_text}"
            }
        },
    ]

    # Lista de tickets nuevos (si los hay)
    if new_tickets:
        ticket_lines = "\n".join(
            [f"  • *Ticket #{t['id']}* — detectado a las {t['time']}" for t in new_tickets]
        )
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Tickets detectados:*\n{ticket_lines}"
            }
        })
        # Botón de acceso directo
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Abrir ServiceTonic", "emoji": True},
                    "url": "https://admincoordinador.plataformagroup.cl/ServiceTonic/login.jsf",
                    "style": "primary"
                }
            ]
        })

    blocks.append({"type": "divider"})

    # Estadísticas del día
    stats_text = (
        f"*Estadísticas del día:*\n"
        f"  • Ciclos de verificación ejecutados: *{total_checks}*\n"
        f"  • Primer ciclo: *{first_check}*\n"
        f"  • Último ciclo: *{last_check}*"
    )
    if session_errors > 0:
        stats_text += f"\n  • ⚠️ Errores de sesión: *{session_errors}*"

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": stats_text}
    })

    # Pie de mensaje
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "🤖 ServiceTonic Monitor Robot  |  GitHub Actions  |  Resumen automático de fin de día"
            }
        ]
    })

    return {"blocks": blocks}


# ── Envío a Slack ──────────────────────────────────────────────────────────────

def send_to_slack(payload: dict) -> bool:
    """Envía el payload al webhook de Slack. Retorna True si fue exitoso."""
    if not SLACK_WEBHOOK:
        print("✗ SLACK_WEBHOOK_URL no configurado. No se puede enviar el resumen.")
        return False

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        SLACK_WEBHOOK,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            if resp.status == 200 and body == "ok":
                print(f"✓ Resumen enviado a Slack correctamente (HTTP {resp.status})")
                return True
            else:
                print(f"✗ Respuesta inesperada de Slack: HTTP {resp.status} — {body}")
                return False
    except urllib.error.HTTPError as e:
        print(f"✗ Error HTTP al enviar a Slack: {e.code} — {e.read().decode()}")
        return False
    except Exception as e:
        print(f"✗ Error al enviar a Slack: {e}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Envía el resumen diario de tickets a Slack.")
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y-%m-%d"),
        help="Fecha a resumir en formato YYYY-MM-DD (default: hoy)"
    )
    args = parser.parse_args()
    target_date = args.date

    print("=" * 60)
    print(f"  RESUMEN DIARIO — {target_date}")
    print("=" * 60)

    # Leer y procesar el log
    summary = parse_log(target_date)
    count   = len(summary["new_tickets"])

    print(f"\n  Ciclos ejecutados hoy:  {summary['total_checks']}")
    print(f"  Tickets nuevos:         {count}")
    if count > 0:
        for t in summary["new_tickets"]:
            print(f"    → Ticket #{t['id']} a las {t['time']}")
    else:
        print("    → No hubo nuevos tickets")
    if summary["session_errors"] > 0:
        print(f"  Errores de sesión:      {summary['session_errors']}")

    # Construir y enviar mensaje
    print("\n  Enviando resumen a Slack...")
    payload = build_slack_message(summary, target_date)
    success = send_to_slack(payload)

    print("\n" + "=" * 60)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
