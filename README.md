# ServiceTonic Monitor Robot

Robot de automatización que monitorea ServiceTonic cada 60 segundos, detecta nuevos tickets asignados al usuario `REDACTED_USER` y envía notificaciones por email a `solange.reyes@prodigio.tech`.

---

## Estructura del proyecto

```
servicetonic_monitor/
├── servicetonic_monitor.py   # Script principal del robot
├── config.json               # Configuración (credenciales, email, SMTP)
├── requirements.txt          # Dependencias Python
├── README.md                 # Este archivo
├── last_tickets.json         # Estado persistido (generado automáticamente)
├── ticket_monitor.log        # Log CSV de ejecuciones (generado automáticamente)
├── robot_debug.log           # Log detallado de debugging (generado automáticamente)
└── screenshots/              # Screenshots de errores (generado automáticamente)
```

---

## Requisitos del sistema

- **Python 3.9+** (recomendado: Python 3.11)
- **pip3** instalado
- Acceso a internet para conectarse a ServiceTonic y enviar emails

---

## Instalación

### 1. Clonar o copiar los archivos

Copiar la carpeta `servicetonic_monitor/` al servidor o máquina donde se ejecutará el robot.

### 2. Instalar dependencias

```bash
cd servicetonic_monitor/
pip3 install -r requirements.txt
```

### 3. Instalar el navegador Chromium para Playwright

```bash
python3 -m playwright install chromium
python3 -m playwright install-deps chromium   # Solo en Linux
```

---

## Configuración

Editar el archivo `config.json` antes de ejecutar el robot:

```json
{
  "servicetonic": {
    "login_url": "https://admincoordinador.plataformagroup.cl/ServiceTonic/login.jsf",
    "tickets_url": "https://admincoordinador.plataformagroup.cl/ServiceTonic/xhtml/agentes/servicedesk/agent_sd.jsf?id=66",
    "username": "REDACTED_USER",
    "password": "REDACTED_PASS"
  },
  "email": {
    "recipient": "solange.reyes@prodigio.tech",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "tu_correo@gmail.com",
    "smtp_password": "tu_contraseña_de_aplicacion",
    "use_tls": true,
    "sender_name": "ServiceTonic Monitor"
  },
  "monitor": {
    "check_interval_seconds": 60,
    "login_retry_attempts": 3,
    "page_load_timeout_ms": 30000,
    "retry_wait_seconds": 30,
    "headless": true
  },
  "files": {
    "state_file": "last_tickets.json",
    "log_file": "ticket_monitor.log",
    "screenshots_dir": "screenshots"
  }
}
```

### Configuración de SMTP (Gmail)

Para usar Gmail como servidor SMTP:

1. Activar la **verificación en dos pasos** en tu cuenta Google.
2. Generar una **contraseña de aplicación**: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Usar esa contraseña de 16 caracteres en `smtp_password`.
4. Completar `smtp_user` con tu dirección Gmail.

> **Nota:** Si no se configuran `smtp_user` y `smtp_password`, el robot seguirá monitoreando y registrando en el log, pero **no enviará emails**.

### Otros proveedores SMTP

| Proveedor | Host | Puerto | TLS |
|-----------|------|--------|-----|
| Gmail | smtp.gmail.com | 587 | true |
| Outlook/Hotmail | smtp-mail.outlook.com | 587 | true |
| Yahoo | smtp.mail.yahoo.com | 587 | true |
| SMTP personalizado | tu.servidor.smtp | 587 | true/false |

---

## Ejecución

### Ejecución local (primer plano)

```bash
cd servicetonic_monitor/
python3 servicetonic_monitor.py
```

El robot mostrará logs en la consola y se detendrá con `Ctrl+C`.

### Ejecución en segundo plano (Linux)

**Opción 1: usando `nohup`**

```bash
cd servicetonic_monitor/
nohup python3 servicetonic_monitor.py > output.log 2>&1 &
echo $! > robot.pid
echo "Robot iniciado con PID: $(cat robot.pid)"
```

Para detener el robot:
```bash
kill $(cat robot.pid)
```

**Opción 2: usando `screen`**

```bash
screen -S servicetonic
cd servicetonic_monitor/
python3 servicetonic_monitor.py
# Presionar Ctrl+A, luego D para desconectar
```

Para reconectar:
```bash
screen -r servicetonic
```

**Opción 3: como servicio systemd (recomendado para producción)**

Crear el archivo `/etc/systemd/system/servicetonic-monitor.service`:

```ini
[Unit]
Description=ServiceTonic Monitor Robot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/ruta/completa/a/servicetonic_monitor
ExecStart=/usr/bin/python3 /ruta/completa/a/servicetonic_monitor/servicetonic_monitor.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Activar y arrancar el servicio:
```bash
sudo systemctl daemon-reload
sudo systemctl enable servicetonic-monitor
sudo systemctl start servicetonic-monitor
sudo systemctl status servicetonic-monitor
```

Ver logs del servicio:
```bash
sudo journalctl -u servicetonic-monitor -f
```

### Ejecución en Windows

**Opción 1: Ventana de comandos**

```cmd
cd C:\ruta\a\servicetonic_monitor
python servicetonic_monitor.py
```

**Opción 2: Tarea programada de Windows**

1. Abrir el **Programador de tareas** (`taskschd.msc`).
2. Crear nueva tarea básica.
3. Acción: Iniciar programa → `python.exe`.
4. Argumentos: `C:\ruta\a\servicetonic_monitor\servicetonic_monitor.py`.
5. Directorio de inicio: `C:\ruta\a\servicetonic_monitor`.
6. Configurar para ejecutar al inicio del sistema.

---

## Configuración de Slack (Incoming Webhook)

El robot envía notificaciones a Slack usando **Incoming Webhooks**, que no requieren instalar ninguna librería adicional (usa `urllib` de la biblioteca estándar de Python).

### Cómo obtener el Webhook URL

1. Ir a [api.slack.com/apps](https://api.slack.com/apps) e iniciar sesión.
2. Hacer clic en **Create New App** → **From scratch**.
3. Asignar un nombre (ej. `ServiceTonic Monitor`) y seleccionar el workspace.
4. En el menú lateral, ir a **Incoming Webhooks** y activarlo.
5. Hacer clic en **Add New Webhook to Workspace** y seleccionar el canal destino.
6. Copiar la URL generada (formato: `https://hooks.slack.com/services/T.../B.../...`).
7. Pegar la URL en `config.json`:

```json
"slack": {
  "webhook_url": "<PEGAR_AQUI_TU_WEBHOOK_URL>",
  "channel": "#tickets",
  "username": "ServiceTonic Bot",
  "icon_emoji": ":ticket:",
  "enabled": true
}
```

### Formato del mensaje en Slack

Cada nuevo ticket genera un mensaje con **Block Kit** de Slack:

```
🎫 Nuevo Ticket Asignado — ID: 66032
────────────────────────────────────────
Ticket ID:        66032
Detectado el:     2026-03-18 22:43:11
Sistema:          Abrir ServiceTonic  ← (enlace clickeable)
────────────────────────────────────────
Mensaje automático generado por ServiceTonic Monitor Robot
```

> Si `webhook_url` está vacío o `enabled` es `false`, el robot omite Slack sin interrumpir el loop.

---

## Formato del log CSV (`ticket_monitor.log`)

El log se escribe en formato CSV, **siempre en modo append** (nunca se sobreescribe):

```
YYYY-MM-DD,HH:MM:SS,FOUND/NOT_FOUND,{ticket_id}
```

**Ejemplos:**

```
2026-03-10,14:00:01,NOT_FOUND,N/A
2026-03-10,14:01:01,FOUND,65603
2026-03-10,14:01:01,FOUND,65604
2026-03-10,14:02:01,NOT_FOUND,N/A
2026-03-10,14:03:01,SESSION_EXPIRED,N/A
```

---

## Comportamiento del robot

| Situación | Comportamiento |
|-----------|---------------|
| Primera ejecución | Registra el estado inicial sin enviar alertas |
| Nuevo ticket detectado | Envía email + escribe `FOUND` en el log |
| Sin cambios | Escribe `NOT_FOUND` en el log |
| Sesión expirada | Re-login automático |
| Error de página | Screenshot + continúa el loop |
| Fallo de email | Log del error + continúa el loop |
| Login fallido 3 veces | Detiene el robot |
| Navegador crasheado | Reinicio automático del navegador |

---

## Idempotencia

El robot es **idempotente**: si se reinicia después de un crash, **no re-enviará emails** para tickets ya notificados. El archivo `last_tickets.json` actúa como fuente de verdad del estado anterior.

---

## Archivos generados automáticamente

| Archivo | Descripción |
|---------|-------------|
| `last_tickets.json` | Estado persistido de tickets conocidos |
| `ticket_monitor.log` | Log CSV de cada ciclo de verificación |
| `robot_debug.log` | Log detallado para debugging |
| `screenshots/` | Screenshots tomados en caso de error |

---

## Solución de problemas

**El robot no puede hacer login:**
- Verificar que las credenciales en `config.json` sean correctas.
- Verificar que la URL de login sea accesible desde el servidor.
- Revisar `screenshots/login_failed_*.png` para ver el estado de la página.

**No se envían emails:**
- Verificar que `smtp_user` y `smtp_password` estén configurados en `config.json`.
- Para Gmail, usar una contraseña de aplicación (no la contraseña normal).
- Revisar `robot_debug.log` para ver el mensaje de error específico.

**La tabla de tickets no se encuentra:**
- Revisar `screenshots/tickets_table_missing_*.png`.
- La URL de tickets puede haber cambiado; actualizar `tickets_url` en `config.json`.

**El robot se detiene inesperadamente:**
- Revisar `robot_debug.log` para el mensaje de error.
- Usar el servicio systemd con `Restart=always` para reinicio automático.
