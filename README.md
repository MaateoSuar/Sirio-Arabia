# Sirio Arabia CRM (Flask)

## Requisitos
- Python 3.10+
- pip
- (Opcional) virtualenv
- Node.js + npm (para instalar Railway CLI)

## Configuración local
1. Crear entorno virtual e instalar dependencias:
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
2. Crear archivo `.env` basado en `.env.example`.
3. Inicializar base local (sqlite por defecto):
```
set FLASK_APP=backend.app
flask db init
flask db migrate -m "init"
flask db upgrade
```
4. Ejecutar:
```
python -m backend.app
```

## Railway
1. Instalar Railway CLI:
```
npm i -g @railway/cli
```
2. Iniciar sesión y crear proyecto:
```
railway login
railway init
```
3. Agregar Postgres como plugin en Railway y obtener `DATABASE_URL` (se inyecta como variable en el servicio).
4. Desplegar:
```
railway up
```

Se recomienda tener dos entornos (development y production) en Railway y usar variables por entorno.
