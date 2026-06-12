FROM python:3.13-slim
WORKDIR /app
COPY . .
EXPOSE 8080
CMD ["python3", "app.py", "8080"]
