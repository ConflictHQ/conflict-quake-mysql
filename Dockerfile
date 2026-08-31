FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
RUN pip install --no-cache-dir "PyMySQL==1.1.1"
COPY db.py app.py ./
COPY data/ ./data/
COPY static/ ./static/
RUN useradd --create-home --uid 10001 astro && chown -R astro:astro /app
USER astro
EXPOSE 8080
CMD ["python", "app.py"]
