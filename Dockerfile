# ─────────────── Dockerfile ───────────────
FROM python:3.9-slim

# 1. Create a non-root user
RUN useradd --create-home spotfireuser \
    && mkdir /app \
    && chown spotfireuser:spotfireuser /app

WORKDIR /app

# 2. Copy requirements and install (if any)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy parsing script into container
COPY parse_spotfire.py /app/
RUN chown spotfireuser:spotfireuser /app/parse_spotfire.py

# 4. Create input and output dirs (owned by spotfireuser)
RUN mkdir /app/dxp_input /app/im_output \
    && chown -R spotfireuser:spotfireuser /app/dxp_input /app/im_output

# 5. Switch to non-root
USER spotfireuser
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 6. Default command: run parser
ENTRYPOINT ["python", "parse_spotfire.py"]
# ────────────────────────────────────────────

