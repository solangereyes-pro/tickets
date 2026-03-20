#!/usr/bin/env python3
"""
Ejecuta UN ciclo completo del robot ServiceTonic Monitor y muestra el log.
Usado por GitHub Actions en cada ejecución programada.
Las credenciales se leen desde variables de entorno (secrets de GitHub Actions)
o desde config.json si las variables no están definidas.
"""
import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"


def patch_config_from_env():
    """
    Sobreescribe los valores de config.json con las variables de entorno
    definidas como secrets en GitHub Actions (si están presentes).
    """
    if not CONFIG_FILE.exists():
        return
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    changed = False
    if os.environ.get("ST_USER"):
        cfg["servicetonic"]["username"] = os.environ["ST_USER"]
        changed = True
    if os.environ.get("ST_PASSWORD"):
        cfg["servicetonic"]["password"] = os.environ["ST_PASSWORD"]
        changed = True
    if os.environ.get("SMTP_USER"):
        cfg["email"]["smtp_user"] = os.environ["SMTP_USER"]
        changed = True
    if os.environ.get("SMTP_PASSWORD"):
        cfg["email"]["smtp_password"] = os.environ["SMTP_PASSWORD"]
        changed = True
    if os.environ.get("SLACK_WEBHOOK_URL"):
        cfg["slack"]["webhook_url"] = os.environ["SLACK_WEBHOOK_URL"]
        changed = True
    if changed:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print("[config] Credenciales actualizadas desde variables de entorno.")


patch_config_from_env()

sys.path.insert(0, str(SCRIPT_DIR))

from servicetonic_monitor import (
    ServiceTonicMonitor,
    load_last_tickets,
    save_last_tickets,
    write_csv_log,
    send_email_notification,
    send_slack_notification,
    logger,
    LOG_FILE,
    STATE_FILE,
)

def run_single_cycle():
    print("\n" + "=" * 62)
    print("  ServiceTonic Monitor — EJECUCIÓN ÚNICA (1 ciclo)")
    print(f"  Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)

    monitor = ServiceTonicMonitor()

    try:
        # ── 1. Iniciar navegador ──────────────────────────────────
        print("\n[PASO 1] Iniciando navegador Chromium (headless)...")
        monitor.start_browser()
        print("         ✓ Navegador listo")

        # ── 2. Login ──────────────────────────────────────────────
        print("\n[PASO 2] Autenticando en ServiceTonic...")
        t0 = time.time()
        if not monitor.login():
            print("         ✗ Login fallido — abortando")
            return
        print(f"         ✓ Login exitoso en {time.time()-t0:.1f}s")
        print(f"         URL: {monitor.page.url}")

        # ── 3. Scraping de tickets ────────────────────────────────
        print("\n[PASO 3] Extrayendo tickets asignados...")
        t1 = time.time()
        current_tickets = monitor.get_all_ticket_ids()
        elapsed = time.time() - t1
        print(f"         ✓ {len(current_tickets)} tickets encontrados en {elapsed:.1f}s")
        for tid in sorted(current_tickets):
            print(f"           • {tid}")

        # ── 4. Comparar con estado anterior ───────────────────────
        print("\n[PASO 4] Comparando con estado anterior...")
        known_tickets = load_last_tickets()

        if not known_tickets:
            print("         Primera ejecución — registrando estado inicial.")
            print(f"         {len(current_tickets)} tickets guardados en last_tickets.json")
            new_tickets = set()
            save_last_tickets(current_tickets)
            write_csv_log("NOT_FOUND", "N/A")
        else:
            new_tickets = current_tickets - known_tickets
            removed_tickets = known_tickets - current_tickets
            print(f"         Tickets conocidos (estado anterior): {len(known_tickets)}")
            print(f"         Tickets actuales:                    {len(current_tickets)}")
            print(f"         Tickets NUEVOS detectados:           {len(new_tickets)}")
            if removed_tickets:
                print(f"         Tickets cerrados/removidos:          {len(removed_tickets)}")

            if new_tickets:
                print(f"\n         *** NUEVOS TICKETS: {sorted(new_tickets)} ***")
                for tid in sorted(new_tickets):
                    detection_time = datetime.now()
                    write_csv_log("FOUND", tid)
                    print(f"         → Email enviado para ticket {tid} (si SMTP configurado)")
                    send_email_notification(tid, detection_time)
                    print(f"         → Slack notificado para ticket {tid} (si webhook configurado)")
                    send_slack_notification(tid, detection_time)
            else:
                write_csv_log("NOT_FOUND", "N/A")
                print("         Sin tickets nuevos en este ciclo.")

            save_last_tickets(current_tickets)

        # ── 5. Mostrar log CSV ────────────────────────────────────
        print("\n[PASO 5] Contenido de ticket_monitor.log:")
        print("         " + "-" * 50)
        if LOG_FILE.exists():
            with open(LOG_FILE) as f:
                lines = f.readlines()
            for line in lines:
                print(f"         {line.rstrip()}")
        else:
            print("         (log vacío)")
        print("         " + "-" * 50)

        print("\n" + "=" * 62)
        print("  CICLO COMPLETADO EXITOSAMENTE ✓")
        print("=" * 62 + "\n")

    except Exception as e:
        print(f"\n  ✗ Error durante la ejecución: {e}")
        import traceback
        traceback.print_exc()
        try:
            monitor.take_screenshot("run_once_error")
        except Exception:
            pass
        sys.exit(1)
    finally:
        monitor.stop_browser()

if __name__ == "__main__":
    run_single_cycle()
