# Mech Royale bot. Build: docker build -t mech-bot .
# Runs with no network, read-only root, 1 CPU, 512 MB, uid 65534; standard library only.
FROM python:3.12.8-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /bot
COPY bot.py nav.bin /bot/
RUN python3 -m py_compile /bot/bot.py && chmod -R a+rX /bot
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
