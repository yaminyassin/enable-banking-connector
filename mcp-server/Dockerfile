FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ebmcp ./ebmcp
RUN useradd -r app && chown -R app /app
USER app
ENV PORT=8000 ENABLEBANKING_KEY_PATH=/secrets/private.pem
EXPOSE 8000
CMD ["python", "-m", "ebmcp.server"]
