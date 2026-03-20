#!/usr/bin/env python3
"""
Script de prueba para verificar que el robot funciona correctamente.
Ejecuta UN ciclo de verificación y muestra los resultados.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Agregar directorio padre al path
sys.path.insert(0, str(Path(__file__).parent))

from servicetonic_monitor import (
    ServiceTonicMonitor,
    load_config,
    load_last_tickets,
    save_last_tickets,
    write_csv_log,
    logger,
    CONFIG,
    ST_TICKETS_URL
)


def test_login_and_scraping():
    """Prueba el login y extracción de tickets."""
    print("\n" + "="*60)
    print("  TEST: ServiceTonic Monitor — Prueba de un ciclo")
    print("="*60)

    monitor = ServiceTonicMonitor()

    try:
        print("\n[1/4] Iniciando navegador...")
        monitor.start_browser()
        print("      ✓ Navegador iniciado")

        print("\n[2/4] Realizando login...")
        if not monitor.login():
            print("      ✗ Login fallido")
            return False
        print("      ✓ Login exitoso")
        print(f"      URL actual: {monitor.page.url}")

        print("\n[3/4] Extrayendo tickets...")
        ticket_ids = monitor.get_all_ticket_ids()
        print(f"      ✓ Tickets encontrados: {len(ticket_ids)}")
        for tid in sorted(ticket_ids):
            print(f"        - {tid}")

        print("\n[4/4] Probando detección de nuevos tickets...")
        known = load_last_tickets()
        if not known:
            print("      Primera ejecución — guardando estado inicial...")
            save_last_tickets(ticket_ids)
            print(f"      ✓ Estado guardado: {len(ticket_ids)} tickets")
        else:
            new_tickets = ticket_ids - known
            print(f"      Tickets conocidos: {len(known)}")
            print(f"      Tickets actuales: {len(ticket_ids)}")
            print(f"      Tickets nuevos: {len(new_tickets)}")
            if new_tickets:
                print(f"      NUEVOS: {sorted(new_tickets)}")
            else:
                print("      Sin tickets nuevos en este ciclo.")

        # Escribir en el log CSV
        write_csv_log("NOT_FOUND", "N/A")
        print(f"\n      ✓ Log CSV actualizado: ticket_monitor.log")

        print("\n" + "="*60)
        print("  RESULTADO: Todas las pruebas pasaron correctamente ✓")
        print("="*60 + "\n")
        return True

    except Exception as e:
        print(f"\n      ✗ Error durante la prueba: {e}")
        import traceback
        traceback.print_exc()
        monitor.take_screenshot("test_error")
        return False
    finally:
        monitor.stop_browser()


if __name__ == "__main__":
    success = test_login_and_scraping()
    sys.exit(0 if success else 1)
