# FARMACULM - Backend Python

Servidor Python para la web + API de productos, pedidos y usuarios registrados.

## Requisitos
- Python 3.10+ instalado

## Ejecutar
```bash
python app.py
```

Si en tu Windows `python` no funciona:
```bash
py app.py
```

La app queda en:
- `http://localhost:8000`

Importante:
- Para que registro/login funcionen sin problemas, abre la web desde `http://localhost:8000`.
- Si usas Live Server, igual funcionará siempre que `app.py` esté corriendo en `8000`.

## Correo de registro automatico
Al registrar un usuario (`POST /api/register`), el backend puede enviar un correo de "registro exitoso".

Variables de entorno SMTP:
- `ENABLE_REGISTER_EMAIL` (opcional, `1` por defecto)
- `SMTP_HOST` (ej: `smtp.gmail.com`)
- `SMTP_PORT` (ej: `587` TLS o `465` SSL)
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM` (correo remitente)
- `SMTP_FROM_NAME` (opcional, por defecto `FARMACULM`)
- `SMTP_TLS` (opcional, `1` por defecto si no usas SSL)
- `SMTP_SSL` (opcional, `0` por defecto)

Ejemplo (PowerShell):
```powershell
$env:ENABLE_REGISTER_EMAIL = "1"
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "tu_correo@gmail.com"
$env:SMTP_PASSWORD = "tu_app_password"
$env:SMTP_FROM = "tu_correo@gmail.com"
$env:SMTP_FROM_NAME = "FARMACULM"
$env:SMTP_TLS = "1"
$env:SMTP_SSL = "0"

py app.py
```

Nota:
- Si usas Gmail, normalmente necesitas "App Password".
- Si SMTP no esta configurado, el registro se guarda igual y solo se omite el envio de correo.

## Endpoints API
- `GET /api/health`
- `GET /api/productos`
- `POST /api/pedidos`
- `POST /api/register`
- `POST /api/login`
- `POST /api/password/request-reset`
- `POST /api/password/confirm-reset`

## Base de datos de usuarios
- Archivo: `data/farmaculm.db`
- Tabla: `usuarios`
- Se crea automáticamente al iniciar el servidor.

## Pruebas rápidas (PowerShell)
Salud:
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/health"
```

Registro:
```powershell
$register = @{
  nombre = "Juan Perez"
  email = "juan.demo@correo.com"
  password = "1234"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/register" -Method Post -ContentType "application/json" -Body $register
```

Login:
```powershell
$login = @{
  email = "juan.demo@correo.com"
  password = "1234"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/login" -Method Post -ContentType "application/json" -Body $login
```

Solicitar codigo de recuperacion:
```powershell
$resetRequest = @{
  email = "juan.demo@correo.com"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/password/request-reset" -Method Post -ContentType "application/json" -Body $resetRequest
```

Confirmar recuperacion con codigo:
```powershell
$resetConfirm = @{
  email = "juan.demo@correo.com"
  code = "123456"
  new_password = "1234"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/password/confirm-reset" -Method Post -ContentType "application/json" -Body $resetConfirm
```

Pedido:
```powershell
$body = @{
  cliente = "Doctor Demo"
  total = 12900
  items = @(
    @{ nombre = "Ibuflash Migran x20"; cantidad = 1; precio = 4700 },
    @{ nombre = "Naproxeno 500mg x20"; cantidad = 1; precio = 5500 }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://localhost:8000/api/pedidos" -Method Post -ContentType "application/json" -Body $body
```

Los pedidos se guardan en `pedidos/pedidos.log`.
