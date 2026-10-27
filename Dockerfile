FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY . .
ENV SITEMIRROR_PORT=8787
EXPOSE 8787
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8787"]
