FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src ./src

RUN pip install --no-cache-dir .

ENV DATABASE_URL=sqlite:///./data/triangulo.db
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "triangulo.main:app", "--host", "0.0.0.0", "--port", "8080"]
