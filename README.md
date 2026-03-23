# ChainerTap Cloud Project 🚀

Este proyecto es la versión funcional y comercial (SaaS) de los bots de Chainers y RollerTap.

## Estructura
- `/backend`: Servidor Python (Flask) que maneja múltiples usuarios y hilos de ejecución.
- `/frontend`: Interfaz web moderna lista para ser subida a Vercel.

## Cómo ejecutarlo localmente

### 1. Backend (El Motor)
1. Ve a la carpeta `backend/`.
2. Asegúrate de tener instalado Flask y Flask-CORS:
   ```bash
   pip install flask flask-cors requests
   ```
3. Ejecuta el servidor:
   ```bash
   python main.py
   ```
   *El servidor correrá en `http://localhost:5000`.*

### 2. Frontend (La Interfaz)
1. Simplemente abre el archivo `frontend/index.html` en tu navegador.


## Notas para subir a la Nube
1. **Frontend:** Puedes subir la carpeta `frontend` a **Vercel** o **Netlify**.
2. **Backend:** Sube la carpeta `backend` a **Railway.app** o **Render.com**.
3. **Base de Datos:** Reemplaza el archivo `db.json` por una conexión a **Supabase** (PostgreSQL) para que los datos sean persistentes y seguros.
4. **Pagos:** Configura el endpoint `/api/buy_sub` para que conecte con el checkout de **Stripe**.
