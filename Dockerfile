FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY firestore_backend.py .
COPY menu ./menu
COPY *.html *.css *.js ./
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
