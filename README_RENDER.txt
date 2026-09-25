Sistema Jurídico DIF - despliegue Render

Build Command: pip install -r requirements.txt
Start Command: gunicorn --bind 0.0.0.0:$PORT app:app
Variable de entorno recomendada: SECRET_KEY (una clave larga y aleatoria).
