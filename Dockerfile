FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["sh", "-c", "python -m scripts.init_db && gunicorn -w 3 -b 0.0.0.0:5000 wsgi:app"]
